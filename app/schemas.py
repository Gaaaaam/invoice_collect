from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


# ─── 发票相关 ───────────────────────────────────────────────────────────────

class InvoiceBase(BaseModel):
    invoice_type: Optional[str] = None
    invoice_number: Optional[str] = None
    issue_date: Optional[str] = None
    seller_name: Optional[str] = None
    buyer_name: Optional[str] = None
    amount: Optional[float] = None
    tax_amount: Optional[float] = None
    total_amount: Optional[float] = None
    items_description: Optional[str] = None
    remarks: Optional[str] = None
    invoice_subcategory: Optional[str] = None
    departure_city: Optional[str] = None
    arrival_city: Optional[str] = None
    departure_time: Optional[str] = None
    arrival_time: Optional[str] = None
    is_transport: bool = False


class InvoiceResponse(InvoiceBase):
    id: int
    filename: str
    file_path: str
    uploaded_at: datetime
    extract_status: str
    extracted_data: Optional[dict] = None

    model_config = {"from_attributes": True}


# ─── 分组相关 ────────────────────────────────────────────────────────────────

class CollectionGroupResponse(BaseModel):
    id: int
    name: str
    category_id: str
    group_type: str
    description: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    sort_order: int
    invoices: list[InvoiceResponse] = []

    model_config = {"from_attributes": True}


# ─── 归集条目相关 ─────────────────────────────────────────────────────────────

class CollectionItemResponse(BaseModel):
    id: int
    invoice_id: int
    category_id: str
    group_id: Optional[int] = None
    classified_by: str
    note: Optional[str] = None

    model_config = {"from_attributes": True}


class MoveInvoiceRequest(BaseModel):
    invoice_id: int
    target_category_id: str
    target_group_id: Optional[int] = None
    note: Optional[str] = None


class BatchMoveInvoicesRequest(BaseModel):
    invoice_ids: list[int]
    target_category_id: str
    target_group_id: Optional[int] = None
    note: Optional[str] = None


# ─── 归集结果全量响应 ──────────────────────────────────────────────────────────

class CategoryResult(BaseModel):
    category_id: str
    category_name: str
    groupable: bool
    groups: list[CollectionGroupResponse] = []
    ungrouped_invoices: list[InvoiceResponse] = []
    total_amount: float = 0.0


class CollectionResult(BaseModel):
    categories: list[CategoryResult]
    unclassified_invoices: list[InvoiceResponse] = []
    processed_at: Optional[datetime] = None


# ─── 配置相关 ────────────────────────────────────────────────────────────────

class CategoryConfig(BaseModel):
    id: str
    name: str
    groupable: bool = False
    group_type: Optional[str] = None
    description: Optional[str] = None


class CategoriesConfig(BaseModel):
    categories: list[CategoryConfig]


class RuleCondition(BaseModel):
    field: str
    match_type: str = Field(..., description="contains / regex / equals")
    value: str


class RuleConfig(BaseModel):
    id: str
    name: str
    priority: int = 50
    conditions: list[RuleCondition]
    condition_logic: str = Field(default="OR", description="AND / OR")
    target_category: str


class RulesConfig(BaseModel):
    rules: list[RuleConfig]


class LLMConfig(BaseModel):
    base_url: str
    api_key: str
    model: str
    timeout: int = 60


class NuExtractConfig(BaseModel):
    host: str
    port: int
    timeout: int = 60


class ExtractionOCRConfig(BaseModel):
    engine: str = "rapidocr_onnx"
    pdf_max_pages: int = 3
    min_text_chars: int = 80


class ExtractionConfig(BaseModel):
    provider: str = "auto"
    use_llm_on_fallback: bool = False
    ocr: ExtractionOCRConfig = Field(default_factory=ExtractionOCRConfig)


class ModelsConfig(BaseModel):
    llm: LLMConfig
    nuextract: NuExtractConfig
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)


class TravelConfig(BaseModel):
    home_city: str = "上海"


# ─── 通用响应 ────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str
    data: Optional[Any] = None


class ProcessRequest(BaseModel):
    invoice_ids: Optional[list[int]] = None
    """不参与本次归集的发票 ID（例如已移入前端历史归档区的发票）。"""
    exclude_invoice_ids: Optional[list[int]] = None
    force_reclassify: bool = False
    use_subcategory: bool = True  # 使用内容分析子类型映射
    use_rules: bool = True        # 使用规则匹配
    use_llm: bool = True          # 使用大模型兜底


class CreateGroupRequest(BaseModel):
    name: str
    category_id: str
    group_type: str = "manual"
    description: Optional[str] = None


class UpdateGroupRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class BatchDeleteInvoicesRequest(BaseModel):
    invoice_ids: list[int]
