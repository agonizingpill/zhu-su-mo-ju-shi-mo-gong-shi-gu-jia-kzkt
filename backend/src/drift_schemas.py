from pydantic import BaseModel, Field, ConfigDict


class Clean(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class DriftConfigInput(Clean):
    # 允许漂移常数 k（相对预测工时的宽松度，0~1）与判决阈值 h（CUSUM 单边门限，>0）
    k_slack: float = Field(ge=0, le=2)
    h_threshold: float = Field(gt=0, le=10)
    min_samples: int = Field(ge=2, le=100)


class RunTrigger(Clean):
    machine: str | None = Field(default=None, min_length=1, max_length=40)


class AlarmRelease(Clean):
    reason: str = Field(min_length=8, max_length=600)
