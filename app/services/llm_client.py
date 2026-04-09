import json
import logging
import re
from typing import Any, Optional

from openai import AsyncOpenAI

from app.models_runtime import load_models_config

logger = logging.getLogger(__name__)


def load_llm_config() -> dict:
    return load_models_config().get("llm") or {}


def is_llm_configured() -> bool:
    """base_url / api_key 已填写且非 models.yml 占位符时返回 True。"""
    cfg = load_llm_config()
    base = str(cfg.get("base_url") or "").strip()
    key = str(cfg.get("api_key") or "").strip()
    if not base or not key:
        return False
    bl, kl = base.lower(), key.lower()
    if "your-base-url" in bl:
        return False
    if "your-api-key" in kl:
        return False
    return True


def _parse_json_object_from_llm(content: str) -> Optional[dict[str, Any]]:
    s = (content or "").strip()
    if not s:
        return None
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I | re.M)
        s = re.sub(r"\s*```\s*$", "", s)
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


class LLMClient:
    def __init__(self):
        cfg = load_llm_config()
        self.model = cfg.get("model", "gpt-4o-mini")
        self.timeout = cfg.get("timeout", 60)
        self.client = AsyncOpenAI(
            base_url=cfg.get("base_url", "https://api.openai.com/v1"),
            api_key=cfg.get("api_key", ""),
            timeout=self.timeout,
        )

    async def classify_invoice_with_raw(
        self,
        invoice_data: dict,
        categories: list[dict],
    ) -> tuple[Optional[str], str]:
        """
        调用 LLM 判断费用大类。返回 (category_id 或 None, 模型原始文本片段)。
        """
        category_list = "\n".join(
            f"- {c['id']}：{c['name']}（{c.get('description', '')}）"
            for c in categories
        )
        invoice_summary = self._format_invoice_summary(invoice_data)

        prompt = f"""你是一名财务专员，请根据以下发票信息，判断该发票应归入哪个费用大类。

费用大类列表：
{category_list}

判断要点：火车票、高铁票、动车票、铁路电子客票、印有「报销凭证」「仅供报销使用」的铁路纸质报销凭证、机票行程单、住宿费、网约车/出租车等城市间或出差交通，一律归为 travel（差旅费）；会议相关归为 meeting；办公耗材与原材料归为 material；其余归为 other。

发票信息：
{invoice_summary}

请直接返回费用大类的 id（仅 travel、meeting、material、other 之一），不要包含任何其他文字或标点。"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=50,
            )
            raw = (response.choices[0].message.content or "").strip()
            result = raw.lower()
            logger.debug(
                "LLM classify_invoice invoice_id=%s raw=%r",
                invoice_data.get("id"),
                raw[:200] if raw else raw,
            )
            valid_ids = {c["id"] for c in categories}
            if result in valid_ids:
                return result, raw
            for cat_id in valid_ids:
                if cat_id in result:
                    return cat_id, raw
            return None, raw
        except Exception as e:
            logger.warning(
                "LLM classify_invoice error invoice_id=%s: %s",
                invoice_data.get("id"),
                e,
            )
            return None, ""

    async def classify_invoice(
        self,
        invoice_data: dict,
        categories: list[dict],
    ) -> Optional[str]:
        """
        根据发票结构化信息，调用 LLM 判断所属费用大类。
        返回 category_id 或 None。
        """
        cat, _ = await self.classify_invoice_with_raw(invoice_data, categories)
        return cat

    async def classify_meeting_group(
        self,
        invoices: list[dict],
    ) -> list[list[int]]:
        """
        对会议费发票进行分组，将同一场会议的发票归为一组。
        返回分组后的发票 id 列表的列表。
        """
        if not invoices:
            return []

        invoice_lines = []
        for inv in invoices:
            line = (
                f"ID={inv['id']} 日期={inv.get('issue_date', '未知')} "
                f"销售方={inv.get('seller_name', '未知')} "
                f"描述={inv.get('items_description', '未知')} "
                f"金额={inv.get('total_amount', 0)}"
            )
            invoice_lines.append(line)

        invoice_text = "\n".join(invoice_lines)

        prompt = f"""你是一名财务专员，以下是多张会议费发票，请将属于同一场会议的发票归为一组。

发票列表：
{invoice_text}

请以 JSON 格式返回分组结果，格式为：
{{"groups": [[id1, id2], [id3], ...]}}

其中每个子数组是一组同一场会议的发票 ID 列表。如果无法判断是否同一场会议，每张发票单独一组。
只返回 JSON，不要包含其他内容。"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=500,
            )
            content = (response.choices[0].message.content or "").strip()
            logger.debug(
                "LLM classify_meeting_group raw (truncated): %r",
                content[:800] if content else content,
            )
            data = json.loads(content)
            return data.get("groups", [[inv["id"]] for inv in invoices])
        except Exception as e:
            logger.warning("LLM classify_meeting_group error: %s", e)
            return [[inv["id"]] for inv in invoices]

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """通用对话接口"""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **kwargs,
        )
        return response.choices[0].message.content.strip()

    async def extract_structured_from_ocr_text(
        self,
        full_text: str,
        field_spec: str,
        *,
        invoice_type_name: str = "",
        max_chars: int = 12000,
    ) -> Optional[dict[str, Any]]:
        """
        根据字段说明从 OCR 全文抽取单个 JSON 对象（与 NuExtract 模板键一致）。
        兼容支持 response_format=json_object 的 OpenAI 兼容接口；失败时降级重试。
        """
        snippet = (full_text or "")[:max_chars]
        hint = f"\n票面类型提示：{invoice_type_name}" if invoice_type_name else ""
        prompt = (
            "以下是通过 OCR 得到的发票全文（可能有错字、缺行或乱序）。请只依据文本抽取信息，不要编造。"
            f"{hint}\n\n"
            "输出要求：仅输出一个 JSON 对象；键名与层级必须与下方「字段结构」说明一致；"
            "无法确定的字段用 null；不要 markdown 代码围栏或其它说明文字。\n\n"
            f"字段结构：\n{field_spec}\n\n"
            "OCR 文本：\n---\n"
            f"{snippet}\n---"
        )

        create_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 4096,
        }
        try:
            response = await self.client.chat.completions.create(
                **create_kwargs,
                response_format={"type": "json_object"},
            )
        except Exception as first_exc:
            logger.debug("LLM json_object mode failed, retrying without: %s", first_exc)
            try:
                response = await self.client.chat.completions.create(**create_kwargs)
            except Exception as e:
                logger.warning("LLM extract_structured_from_ocr_text error: %s", e)
                return None

        content = (response.choices[0].message.content or "").strip()
        return _parse_json_object_from_llm(content)

    @staticmethod
    def _format_invoice_summary(data: dict) -> str:
        fields = [
            ("发票类型", data.get("invoice_type")),
            ("开票日期", data.get("issue_date")),
            ("销售方", data.get("seller_name")),
            ("购买方", data.get("buyer_name")),
            ("货物/服务描述", data.get("items_description")),
            ("备注", data.get("remarks")),
            ("价税合计", data.get("total_amount")),
            ("车次/航班", data.get("train_number")),
            ("出发城市", data.get("departure_city")),
            ("到达城市", data.get("arrival_city")),
            ("抽取识别类型", data.get("invoice_type_detected")),
        ]
        return "\n".join(f"{k}: {v}" for k, v in fields if v)


_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


def reset_llm_client() -> None:
    """配置变更后调用，重置客户端实例"""
    global _llm_client
    _llm_client = None
