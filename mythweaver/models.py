"""Live model configuration, encrypted provider keys, and task-specific routing."""
import base64
import hashlib
from types import SimpleNamespace
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
import httpx
from sqlalchemy import select

from .db import ModelProvider, PlatformConfig
from .definitions import DomainError


def assistant_message(message):
    """Preserve provider protocol fields without exposing them as user-facing answers."""
    result={'role':'assistant','content':message.get('content')}
    if message.get('tool_calls'):
        result['tool_calls']=message['tool_calls']
    if isinstance(message.get('reasoning_content'),str):
        result['reasoning_content']=message['reasoning_content']
    return result


def validate_url(value):
    url = urlsplit(value)
    if (not url.hostname or url.username or url.password or url.query or url.fragment or
        (url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1")))):
        raise DomainError("INVALID_PROVIDER", "远程地址须为 HTTPS，本机可用 HTTP；不可包含账号、查询参数或片段")
    return value.rstrip("/")


class Models:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.session_secret.encode()).digest()))

    def public(self, provider):
        return {"id":provider.id,"name":provider.name,"base_url":provider.base_url,"models":provider.models,
                "enabled":provider.enabled,"has_key":bool(provider.encrypted_key),"token_parameter":provider.token_parameter,
                "version":provider.version}

    def view(self):
        with self.store.session() as db:
            config = db.get(PlatformConfig, "model_routing")
            return {"providers":[self.public(p) for p in db.scalars(select(ModelProvider).order_by(ModelProvider.created_at))],
                    "routing": config.value if config else {},"version":config.version if config else 0,
                    "environment_available": self.settings.agent_configured}

    def resolve(self, task="planner"):
        with self.store.session() as db:
            config = db.get(PlatformConfig, "model_routing")
            route = (config.value if config else {}).get(task)
            if route:
                provider = db.get(ModelProvider, route["provider_id"])
                if not provider or not provider.enabled or not provider.encrypted_key:
                    raise DomainError("MODEL_NOT_CONFIGURED", "分配的模型服务未启用或没有密钥", 409)
                if route["model"] not in provider.models:
                    raise DomainError("MODEL_NOT_CONFIGURED", "任务模型已不在服务商的模型列表中", 409)
                try:
                    key = self.cipher.decrypt(provider.encrypted_key.encode()).decode()
                except InvalidToken:
                    raise DomainError("KEY_UNREADABLE", "密钥无法解密，请重新填写；更换 SESSION_SECRET 会影响解密", 409) from None
                return SimpleNamespace(llm_base_url=provider.base_url,llm_api_key=key,llm_model=route["model"],
                                       max_tokens=route.get("max_tokens",4096),timeout=route.get("timeout",60),
                                       token_parameter=provider.token_parameter,provider_id=provider.id)
        if self.settings.agent_configured:
            return SimpleNamespace(llm_base_url=validate_url(self.settings.llm_base_url),llm_api_key=self.settings.llm_api_key,
                                   llm_model=self.settings.llm_model,max_tokens=4096,timeout=60,
                                   token_parameter="max_completion_tokens",provider_id="environment")
        raise DomainError("MODEL_NOT_CONFIGURED", "请在模型管理中添加服务商，并分配到相应任务", 409)

    def available(self, task="planner"):
        try:
            return self.resolve(task).llm_model
        except DomainError:
            return None

    async def test(self, provider_id):
        with self.store.session() as db:
            provider = db.get(ModelProvider,provider_id)
            if not provider or not provider.encrypted_key:
                raise DomainError("MODEL_NOT_CONFIGURED", "请先保存服务商及密钥")
            url = validate_url(provider.base_url)
            try:
                key = self.cipher.decrypt(provider.encrypted_key.encode()).decode()
            except InvalidToken:
                raise DomainError("KEY_UNREADABLE", "请重新保存服务商密钥") from None
        try:
            async with httpx.AsyncClient(timeout=15,follow_redirects=False,trust_env=False) as client:
                async with client.stream("GET",url+"/models",headers={"Authorization":"Bearer "+key}) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body)>262144:
                            raise DomainError("PROVIDER_RESPONSE_TOO_LARGE","模型列表超过限制")
                    import json
                    data=json.loads(body)
                    models=[str(m["id"])[:200] for m in data.get("data",[]) if isinstance(m,dict) and "id" in m][:200]
            return {"ok":True,"models":models,"message":"模型列表接口可访问；具体模型的工具调用仍需实际任务验证"}
        except (httpx.HTTPError,ValueError,TypeError):
            raise DomainError("PROVIDER_TEST_FAILED","服务商模型列表请求失败；检查地址和密钥，部分服务不提供 /models",502) from None
