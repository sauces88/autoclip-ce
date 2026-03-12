"""
LLM管理器 - 统一管理多个模型提供商
"""
import json
import logging
import os
from typing import Dict, Any, Optional, List
from pathlib import Path

from .llm_providers import (
    LLMProvider, LLMProviderFactory, ProviderType, 
    ModelInfo, LLMResponse
)

logger = logging.getLogger(__name__)

class LLMManager:
    """LLM管理器 - 支持模型池轮换 + 跨提供商回退"""

    def __init__(self, settings_file: Optional[Path] = None):
        self.settings_file = settings_file or self._get_default_settings_file()
        self.current_provider: Optional[LLMProvider] = None
        self.fallback_provider: Optional[LLMProvider] = None
        self.model_pool: List[LLMProvider] = []      # 主提供商的多模型池
        self._pool_index: int = 0                     # 当前使用的模型索引
        self.fallback_pool: List[LLMProvider] = []    # 二级回退模型池（百炼等）
        self._fallback_pool_index: int = 0
        self.settings = self._load_settings()
        self._initialize_provider()
    
    def _get_default_settings_file(self) -> Path:
        """获取默认设置文件路径"""
        current_file = Path(__file__)
        project_root = current_file.parent.parent.parent  # backend/core -> backend -> project_root
        return project_root / "data" / "settings.json"
    
    def _load_settings(self) -> Dict[str, Any]:
        """加载设置"""
        default_settings = {
            "llm_provider": "dashscope",
            "dashscope_api_key": "",
            "openai_api_key": "",
            "gemini_api_key": "",
            "siliconflow_api_key": "",
            "model_name": "qwen-plus",
            "chunk_size": 5000,
            "min_score_threshold": 0.7
        }
        
        if self.settings_file.exists():
            try:
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    saved_settings = json.load(f)
                    default_settings.update(saved_settings)
            except Exception as e:
                logger.warning(f"加载设置文件失败: {e}")
        
        return default_settings
    
    def _save_settings(self):
        """保存设置"""
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存设置失败: {e}")
            raise
    
    def _create_provider_instance(self, provider_type: ProviderType, model_name: str) -> Optional[LLMProvider]:
        """根据类型和模型名创建 provider 实例"""
        api_key = self._get_api_key_for_provider(provider_type)
        if not api_key:
            return None
        extra_kwargs = {}
        if provider_type == ProviderType.OPENAI:
            base_url = self.settings.get("openai_base_url", "")
            if base_url:
                extra_kwargs["base_url"] = base_url
        elif provider_type == ProviderType.BAILIAN:
            base_url = self.settings.get("bailian_base_url", "https://coding.dashscope.aliyuncs.com/v1")
            extra_kwargs["base_url"] = base_url
        elif provider_type == ProviderType.TENCENT:
            base_url = self.settings.get("tencent_base_url", "https://api.lkeap.cloud.tencent.com/coding/v3")
            extra_kwargs["base_url"] = base_url
        return LLMProviderFactory.create_provider(
            provider_type, api_key, model_name, **extra_kwargs
        )

    def _initialize_provider(self):
        """初始化主 provider、模型池和回退 provider"""
        # --- 主 provider + 模型池 ---
        self.model_pool = []
        self._pool_index = 0
        try:
            provider_type = ProviderType(
                self.settings.get("llm_provider") or os.getenv("API_LLM_PROVIDER", "dashscope")
            )
            model_name = (
                self.settings.get("model_name") or
                os.getenv("API_MODEL_NAME", "qwen-plus")
            )

            # 构建模型池（同提供商多模型轮换）
            pool_key_map = {
                ProviderType.GEMINI: "gemini_model_pool",
                ProviderType.TENCENT: "tencent_model_pool",
                ProviderType.BAILIAN: "bailian_model_pool",
            }
            pool_key = pool_key_map.get(provider_type)
            pool_names = self.settings.get(pool_key, []) if pool_key else []
            if pool_names:
                # 确保主模型在池首位
                if model_name not in pool_names:
                    pool_names = [model_name] + pool_names
                for name in pool_names:
                    p = self._create_provider_instance(provider_type, name)
                    if p:
                        self.model_pool.append(p)
                if self.model_pool:
                    self.current_provider = self.model_pool[0]
                    logger.info(
                        f"已初始化 {provider_type.value} 模型池: {[p.model_name for p in self.model_pool]}"
                    )
                else:
                    logger.warning(f"{provider_type.value} 模型池为空，未找到API密钥")
            else:
                self.current_provider = self._create_provider_instance(provider_type, model_name)
                if self.current_provider:
                    self.model_pool = [self.current_provider]
                    logger.info(f"已初始化主 provider: {provider_type.value}，模型: {model_name}")
                else:
                    logger.warning(f"未找到{provider_type.value}的API密钥")
        except Exception as e:
            logger.error(f"初始化主 provider 失败: {e}")
            self.current_provider = None

        # --- 二级回退模型池（排除主 provider，避免重复） ---
        self.fallback_pool = []
        self._fallback_pool_index = 0
        fallback_pools = [
            ("bailian_api_key", "bailian_model_pool", ProviderType.BAILIAN, "百炼"),
            ("tencent_api_key", "tencent_model_pool", ProviderType.TENCENT, "腾讯"),
            ("gemini_api_key", "gemini_model_pool", ProviderType.GEMINI, "Gemini"),
        ]
        for key_field, pool_field, ptype, label in fallback_pools:
            if ptype == provider_type:
                continue  # 跳过主 provider，已在主池中
            pool_names = self.settings.get(pool_field, [])
            api_key = self.settings.get(key_field, "")
            if api_key and pool_names:
                try:
                    for name in pool_names:
                        p = self._create_provider_instance(ptype, name)
                        if p:
                            self.fallback_pool.append(p)
                    logger.info(
                        f"已初始化{label}模型池: {[p.model_name for p in self.fallback_pool if isinstance(p, LLMProviderFactory._providers[ptype])]}"
                    )
                except Exception as e:
                    logger.warning(f"初始化{label}模型池失败: {e}")

        # --- 回退 provider（最终兜底） ---
        self.fallback_provider = None
        fallback_type_str = self.settings.get("llm_fallback_provider", "")
        fallback_model = self.settings.get("fallback_model_name", "")
        if fallback_type_str and fallback_model:
            try:
                fallback_type = ProviderType(fallback_type_str)
                self.fallback_provider = self._create_provider_instance(fallback_type, fallback_model)
                if self.fallback_provider:
                    logger.info(f"已初始化兜底 provider: {fallback_type.value}，模型: {fallback_model}")
            except Exception as e:
                logger.warning(f"初始化兜底 provider 失败: {e}")
    
    def _get_api_key_for_provider(self, provider_type: ProviderType) -> Optional[str]:
        """获取指定提供商的API密钥，优先读settings.json，回退到环境变量"""
        key_mapping = {
            ProviderType.DASHSCOPE: ("dashscope_api_key", "API_DASHSCOPE_API_KEY"),
            ProviderType.OPENAI: ("openai_api_key", "OPENAI_API_KEY"),
            ProviderType.GEMINI: ("gemini_api_key", "GEMINI_API_KEY"),
            ProviderType.SILICONFLOW: ("siliconflow_api_key", "SILICONFLOW_API_KEY"),
            ProviderType.BAILIAN: ("bailian_api_key", "BAILIAN_API_KEY"),
            ProviderType.TENCENT: ("tencent_api_key", "TENCENT_API_KEY"),
        }

        entry = key_mapping.get(provider_type)
        if not entry:
            return None

        settings_key, env_key = entry
        # 优先 settings.json
        key = self.settings.get(settings_key, "")
        # 回退到环境变量
        if not key:
            key = os.getenv(env_key, "")
        return key or None
    
    def update_settings(self, new_settings: Dict[str, Any]):
        """更新设置"""
        self.settings.update(new_settings)
        self._save_settings()
        self._initialize_provider()
    
    def set_provider(self, provider_type: ProviderType, api_key: str, model_name: str):
        """设置提供商"""
        try:
            # 更新设置
            provider_settings = {
                "llm_provider": provider_type.value,
                "model_name": model_name
            }
            
            # 更新对应提供商的API密钥
            key_mapping = {
                ProviderType.DASHSCOPE: "dashscope_api_key",
                ProviderType.OPENAI: "openai_api_key",
                ProviderType.GEMINI: "gemini_api_key",
                ProviderType.SILICONFLOW: "siliconflow_api_key",
            }
            
            key_name = key_mapping.get(provider_type)
            if key_name:
                provider_settings[key_name] = api_key
            
            self.update_settings(provider_settings)
            
            # 创建新的提供商实例
            self.current_provider = LLMProviderFactory.create_provider(
                provider_type, api_key, model_name
            )
            
            logger.info(f"已切换到{provider_type.value}提供商，模型: {model_name}")
            
        except Exception as e:
            logger.error(f"设置提供商失败: {e}")
            raise
    
    def call(self, prompt: str, input_data: Any = None, **kwargs) -> str:
        """调用LLM"""
        if not self.current_provider:
            raise ValueError("未配置LLM提供商，请在设置页面配置API密钥")
        
        try:
            response = self.current_provider.call(prompt, input_data, **kwargs)
            return response.content
        except Exception as e:
            logger.error(f"LLM调用失败: {str(e).splitlines()[0][:150]}")
            raise
    
    @staticmethod
    def _is_rate_limit_error(err_str: str) -> bool:
        """判断是否为 429 限流错误"""
        lower = err_str.lower()
        return "429" in err_str or "quota" in lower or "rate" in lower or "resource_exhausted" in lower

    @staticmethod
    def _is_auth_error(err_str: str) -> bool:
        """判断是否为认证/授权错误（403、key 泄露、无效 key 等），这类错误重试同一 provider 无意义"""
        lower = err_str.lower()
        return ("403" in err_str or "401" in err_str
                or "leaked" in lower or "revoked" in lower
                or "invalid" in lower and "key" in lower
                or "permission" in lower or "forbidden" in lower
                or "unauthorized" in lower)

    def call_with_retry(self, prompt: str, input_data: Any = None, max_retries: int = 3, **kwargs) -> str:
        """带重试 + 模型池轮换 + 跨提供商回退的 LLM 调用

        流程：
        1. 用当前模型调用
        2. 遇到 429/认证错误 → 轮换到池中下一个模型（不等待）
        3. 池中所有模型都失败 → 切到回退模型池（不等待）
        4. 回退也失败 → 等待后重试
        """
        import time
        import re as _re

        for attempt in range(max_retries):
            # --- 尝试当前 provider ---
            try:
                return self.call(prompt, input_data, **kwargs)
            except ValueError:
                raise
            except Exception as e:
                err_str = str(e)
                is_rate_limit = self._is_rate_limit_error(err_str)
                is_auth = self._is_auth_error(err_str)
                should_fallback = is_rate_limit or is_auth

                if should_fallback:
                    reason = "认证失败" if is_auth else "限流"
                    # --- 429/auth: 尝试池中其他模型（auth 错误跳过同 provider） ---
                    if not is_auth and len(self.model_pool) > 1:
                        for offset in range(1, len(self.model_pool)):
                            next_idx = (self._pool_index + offset) % len(self.model_pool)
                            next_provider = self.model_pool[next_idx]
                            logger.warning(
                                f"模型 {self.current_provider.model_name} {reason}，"
                                f"轮换到 {next_provider.model_name}"
                            )
                            try:
                                response = next_provider.call(prompt, input_data, **kwargs)
                                self._pool_index = next_idx
                                self.current_provider = next_provider
                                return response.content
                            except Exception as pool_e:
                                if self._is_rate_limit_error(str(pool_e)):
                                    continue
                                logger.warning(f"模型 {next_provider.model_name} 失败: {str(pool_e)[:200]}")
                                break

                    # --- 主池失败，尝试回退模型池 ---
                    if self.fallback_pool:
                        for offset in range(len(self.fallback_pool)):
                            idx = (self._fallback_pool_index + offset) % len(self.fallback_pool)
                            bp = self.fallback_pool[idx]
                            logger.warning(f"主模型池{reason}，尝试回退 {bp.model_name}")
                            try:
                                response = bp.call(prompt, input_data, **kwargs)
                                self._fallback_pool_index = (idx + 1) % len(self.fallback_pool)
                                return response.content
                            except Exception as bp_e:
                                if self._is_rate_limit_error(str(bp_e)):
                                    continue
                                logger.warning(f"回退 {bp.model_name} 失败: {str(bp_e)[:200]}")
                                break

                    # --- 回退池也失败，切到最终兜底 provider ---
                    if self.fallback_provider:
                        logger.warning(f"所有模型池失败，切换到兜底 provider")
                        try:
                            response = self.fallback_provider.call(prompt, input_data, **kwargs)
                            return response.content
                        except Exception as fb_e:
                            logger.warning(f"兜底 provider 也失败: {str(fb_e)[:200]}")

                # --- 最后一次重试也失败 ---
                if attempt == max_retries - 1:
                    logger.error(f"LLM调用在{max_retries}次重试后彻底失败。")
                    raise

                # --- 认证错误不需要等待重试（换 provider 就行），直接进入下一轮 ---
                if is_auth:
                    logger.warning(f"主 provider 认证失败，直接重试 ({attempt + 1}/{max_retries})")
                    continue

                # --- 等待后重试 ---
                wait_time = 2 ** attempt
                if is_rate_limit:
                    match = _re.search(r'retry\s+in\s+([\d.]+)s', err_str, _re.IGNORECASE)
                    if match:
                        wait_time = min(float(match.group(1)) + 2, 120)
                    else:
                        wait_time = max(30, 2 ** attempt * 15)
                    logger.warning(f"全部限流，等待 {wait_time:.0f}s 后重试 ({attempt + 1}/{max_retries})")
                else:
                    logger.warning(f"第{attempt + 1}次调用失败，{wait_time}s后重试: {err_str.splitlines()[0][:120]}")
                time.sleep(wait_time)
        return ""
    
    def test_provider_connection(self, provider_type: ProviderType, api_key: str, model_name: str) -> bool:
        """测试提供商连接"""
        try:
            provider = LLMProviderFactory.create_provider(provider_type, api_key, model_name)
            return provider.test_connection()
        except Exception as e:
            logger.error(f"测试{provider_type.value}连接失败: {e}")
            return False
    
    def get_current_provider_info(self) -> Dict[str, Any]:
        """获取当前提供商信息"""
        if not self.current_provider:
            return {"provider": None, "model": None, "available": False}
        
        provider_type = ProviderType(self.settings.get("llm_provider", "dashscope"))
        model_name = self.settings.get("model_name", "qwen-plus")
        
        return {
            "provider": provider_type.value,
            "model": model_name,
            "available": True,
            "display_name": self._get_provider_display_name(provider_type)
        }
    
    def _get_provider_display_name(self, provider_type: ProviderType) -> str:
        """获取提供商显示名称"""
        display_names = {
            ProviderType.DASHSCOPE: "阿里通义千问",
            ProviderType.OPENAI: "OpenAI",
            ProviderType.GEMINI: "Google Gemini",
            ProviderType.SILICONFLOW: "硅基流动",
            ProviderType.BAILIAN: "阿里云百炼",
            ProviderType.TENCENT: "腾讯云",
        }
        return display_names.get(provider_type, provider_type.value)
    
    def get_all_available_models(self) -> Dict[str, List[Dict[str, Any]]]:
        """获取所有可用模型"""
        all_models = LLMProviderFactory.get_all_available_models()
        result = {}
        
        for provider_type, models in all_models.items():
            provider_name = provider_type.value
            result[provider_name] = [
                {
                    "name": model.name,
                    "display_name": model.display_name,
                    "max_tokens": model.max_tokens,
                    "description": model.description
                }
                for model in models
            ]
        
        return result
    
    def parse_json_response(self, response: str) -> Any:
        """解析JSON响应（保持与原LLMClient的兼容性）"""
        if not self.current_provider:
            raise ValueError("未配置LLM提供商")
        
        # 这里可以复用原LLMClient的JSON解析逻辑
        # 为了保持兼容性，我们创建一个临时的LLMClient实例
        from ..utils.llm_client import LLMClient
        temp_client = LLMClient()
        return temp_client.parse_json_response(response)

# 全局LLM管理器实例
_llm_manager: Optional[LLMManager] = None

def get_llm_manager() -> LLMManager:
    """获取全局LLM管理器实例"""
    global _llm_manager
    if _llm_manager is None:
        _llm_manager = LLMManager()
    return _llm_manager

def initialize_llm_manager(settings_file: Optional[Path] = None) -> LLMManager:
    """初始化LLM管理器"""
    global _llm_manager
    _llm_manager = LLMManager(settings_file)
    return _llm_manager
