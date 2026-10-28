from .models import LABELS, ProviderResult

HELP = """以图搜图
/搜图 [yandex|saucenao|ascii2d]：指定来源，省略时聚合通用来源
/搜本子 [严格]：通过 SoutuBot 搜索漫画、本子
/搜番号：通过 AVScan 识别截图番号
/搜图 帮助 · /搜图 状态
命令可附图或引用图片；无图时请在 60 秒内发图。
多图默认搜索第一张；不会自动搜索普通图片。
AI 可调用 image_search 工具，返回结构化结果。"""


def format_result(result: ProviderResult) -> str:
    lines = [f"【{LABELS[result.platform]}】"]
    if result.status == "error":
        lines.append(result.error["message"])
    elif not result.items:
        lines.append("没有找到匹配结果。")
    else:
        for index, item in enumerate(result.items, 1):
            lines.append(f"{index}. {item.title}")
            details = []
            if item.similarity is not None:
                details.append(f"相似度 {item.similarity:.2f}%")
            if result.platform == "soutubot":
                details.append(f"来源 {item.source}")
                if item.extra.get("page"):
                    details.append(f"第 {item.extra['page']} 页")
            frames = item.extra.get("frames", [])
            times = [f["timestamp"] for f in frames if f.get("timestamp")]
            if times:
                details.append("时间 " + " / ".join(times))
            if details:
                lines.append(" · ".join(details))
            if item.url:
                lines.append(item.url)
    if result.result_url:
        lines.append("结果页：" + result.result_url)
    return "\n".join(lines)
