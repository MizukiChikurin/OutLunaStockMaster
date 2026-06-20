"""运行指标监控。"""

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OperationMetric:
    """单次操作指标。"""

    operation: str
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    success: bool = False
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def finish(self, success: bool = True, error: str = "", **metadata: Any) -> None:
        """结束操作记录。"""
        self.end_time = time.time()
        self.success = success
        self.error = error
        self.metadata.update(metadata)

    @property
    def duration(self) -> float:
        """操作耗时（秒）。"""
        end = self.end_time or time.time()
        return end - self.start_time


class MetricsCollector:
    """指标收集器。"""

    def __init__(self):
        self.operations: list[OperationMetric] = []
        self.data_source_stats: dict[str, dict[str, Any]] = {}

    def start_operation(self, operation: str, **metadata: Any) -> OperationMetric:
        """开始记录一次操作。"""
        metric = OperationMetric(operation=operation, metadata=metadata)
        self.operations.append(metric)
        return metric

    def record_data_source_call(
        self,
        provider: str,
        method: str,
        success: bool,
        duration: float,
    ) -> None:
        """记录数据源调用统计。"""
        key = f"{provider}.{method}"
        stats = self.data_source_stats.setdefault(
            key,
            {"provider": provider, "method": method, "calls": 0, "success": 0, "fail": 0, "total_time": 0.0},
        )
        stats["calls"] += 1
        if success:
            stats["success"] += 1
        else:
            stats["fail"] += 1
        stats["total_time"] += duration

    def get_summary(self) -> dict[str, Any]:
        """获取指标摘要。"""
        total_ops = len(self.operations)
        successful_ops = sum(1 for op in self.operations if op.success)
        total_duration = sum(op.duration for op in self.operations)

        return {
            "total_operations": total_ops,
            "successful_operations": successful_ops,
            "failed_operations": total_ops - successful_ops,
            "total_duration": total_duration,
            "average_duration": total_duration / total_ops if total_ops > 0 else 0,
            "data_source_calls": self.data_source_stats,
        }

    def get_recent_operations(self, n: int = 10) -> list[dict[str, Any]]:
        """获取最近的操作记录。"""
        recent = self.operations[-n:]
        return [
            {
                "operation": op.operation,
                "duration": op.duration,
                "success": op.success,
                "error": op.error,
                "metadata": op.metadata,
            }
            for op in recent
        ]


# 全局指标收集器
metrics = MetricsCollector()
