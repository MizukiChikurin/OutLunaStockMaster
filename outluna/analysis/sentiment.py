"""情绪分析器。"""

from outluna.analysis.base import AnalyzerBase
from outluna.analysis.context import AnalysisContext
from outluna.data.gateway import DataGateway
from outluna.data.models import AnalyzerResult


class SentimentAnalyzer(AnalyzerBase):
    """情绪分析器。

    以 akshare 新闻数据为主要情绪来源，
    以 Kimi Datasource 的公司公告作为补充信息源。
    当新闻源不可用时，以公告数据或中性提示替代。
    """

    dimension = "sentiment"

    def __init__(self, gateway: DataGateway):
        super().__init__(gateway)

    async def analyze(self, symbol: str, context: AnalysisContext | None = None) -> AnalyzerResult:
        """执行情绪分析。"""
        signals: list[str] = []

        try:
            news = self.gateway.get_news(symbol, days=7, limit=20)
        except Exception as exc:
            return AnalyzerResult(
                dimension=self.dimension,
                data={"error": str(exc)},
                signals=["新闻获取失败"],
                summary="新闻获取失败，无法完成情绪分析。",
            )

        try:
            announcements = self.gateway.get_announcements(symbol, days=30)
        except Exception:
            announcements = None

        data = {
            "news_count": len(news),
            "news": news[:10],
            "announcements": announcements,
        }

        if not news:
            if announcements is not None and not announcements.empty:
                signals.append(f"近 30 日公告 {len(announcements)} 条")
            else:
                signals.append("近期无相关新闻与公告，情绪中性")
        else:
            signals.append(f"近 7 日相关新闻 {len(news)} 条")
            positive_words = ["增长", "上涨", "利好", "突破", "强劲", "超预期", "回购", "增持"]
            negative_words = ["下跌", "亏损", "利空", "暴雷", "减持", "裁员", "诉讼", "处罚"]

            positive_count = 0
            negative_count = 0
            for item in news:
                title = item.get("title", "")
                for word in positive_words:
                    if word in title:
                        positive_count += 1
                for word in negative_words:
                    if word in title:
                        negative_count += 1

            if positive_count > negative_count:
                signals.append(f"新闻偏正面（正面 {positive_count}，负面 {negative_count}）")
            elif negative_count > positive_count:
                signals.append(f"新闻偏负面（正面 {positive_count}，负面 {negative_count}）")
            else:
                signals.append("新闻情绪中性")

        summary = "关键信号：" + "；".join(signals) if signals else "暂无明确信号。"

        return AnalyzerResult(
            dimension=self.dimension,
            data=data,
            signals=signals,
            summary=summary,
        )
