"""Wire types: required nullable fields are deliberately declared without defaults."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field

class Wire(BaseModel):
    model_config=ConfigDict(extra='forbid')
class ErrorInfo(Wire):
    code:str
    message:str
    retryable:bool
class ErrorDetail(ErrorInfo):
    request_id:str
    record_id:str|None
    operation_id:str|None
    related_export_id:str|None
    record_acceptance:Literal['unknown','not_accepted','accepted']
    modifiable_rejection:bool
class ErrorResponse(Wire):
    error:ErrorDetail
class StepState(Wire):
    status:Literal['blocked','queued','running','retry_wait','save_pending','awaiting_manual','succeeded']
    automatic_attempts:int=Field(ge=0,le=3)
    manual_attempts:int=Field(ge=0)
    active_mode:Literal['automatic','manual']|None
    next_retry_at:str|None
    last_error:ErrorInfo|None
class Steps(Wire):
    body:StepState
    title:StepState
    tags:StepState
Kind=Literal['continue','title_edit','title_regenerate','tag_delete','record_delete','export_create']
class OperationSummary(Wire):
    id:str
    kind:Kind
    status:Literal['queued','running','succeeded','failed']
    error:ErrorInfo|None
class RecordListItem(Wire):
    id:str
    revision:int=Field(ge=1)
    title_revision:int=Field(ge=0)
    first_sent_at:str
    sent_utc_offset_minutes:int
    record_date:str
    body_format:Literal['markdown']
    body_available:bool
    body_preview:str|None
    title:str|None
    tags:list[str]|None
    catalog_version:str
    steps:Steps
    is_complete:bool
    last_title_operation:OperationSummary|None
    available_actions:list[Literal['read_body','continue','edit_title','regenerate_title','delete_tag','delete_record']]
    updated_at:str
class RecordSnapshot(RecordListItem):
    body_markdown:str|None
class Operation(OperationSummary):
    record_id:str|None
    target_steps:list[Literal['body','title','tags']]
    created_at:str
    updated_at:str
    result_record_revision:int|None
    deleted_record_id:str|None
    export_id:str|None
class OperationResponse(Wire):
    operation:Operation
    record:RecordSnapshot|None
class RecordPage(Wire):
    view:Literal['time','category']
    tag_ids:list[str]
    record_date:str|None
    items:list[RecordListItem]
    next_cursor:str|None
    has_more:bool
class SubmissionResponse(Wire):
    record:RecordSnapshot
    replayed:bool
class PendingResponse(Wire):
    record:RecordSnapshot|None
class ExcludedRecord(Wire):
    id:str
    first_sent_at:str
    title:str|None
    status:Literal['processing','awaiting_manual']
class ExportTask(Wire):
    id:str
    operation_id:str
    revision:int=Field(ge=1)
    format:Literal['zip_markdown']
    scope:Literal['all_completed']
    status:Literal['queued','preparing','ready','failed','expired']
    snapshot_at:str|None
    total_records:int|None
    processed_records:int=Field(ge=0)
    excluded_incomplete_records:list[ExcludedRecord]|None
    file_name:str|None
    file_bytes:int|None
    sha256:str|None
    expires_at:str|None
    error:ErrorInfo|None
