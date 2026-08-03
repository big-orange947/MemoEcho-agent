from app.services.qq_reply_formatter import QqReplyFormatter


def test_main_console_keeps_question_mark() -> None:
    """主控台问句必须保留问号，避免清洗后被误读为陈述句。"""
    formatter = QqReplyFormatter()

    assert formatter.format("明天下午方便吗？", "question", main_console_mode=True) == ["明天下午方便吗？"]


def test_main_console_respects_explicit_bubble_lines() -> None:
    """模型显式换行代表连续气泡，格式化器不得将其重新合并。"""
    formatter = QqReplyFormatter()

    assert formatter.format("我先问下\n晚点回你", "explicit-lines", main_console_mode=True) == ["我先问下", "晚点回你"]


def test_main_console_splits_only_long_text_with_natural_pause() -> None:
    """主控台仅对过长且存在自然停顿的文本补充分段，不按固定字符硬切。"""
    formatter = QqReplyFormatter()
    message = "明天晚上七点到九点我可以，地点就在图书馆门口，到时候见面后再一起确认下周安排，要是有变动提前在群里说一声"

    parts = formatter.format(message, "natural-split", main_console_mode=True)

    assert len(parts) == 2
    assert "".join(parts).replace("，", "") == message.replace("，", "")
    assert parts[0].endswith(("门口", "安排"))
    assert parts[1].startswith(("到时候", "要是"))


def test_main_console_never_hard_cuts_without_semantic_boundary() -> None:
    """没有自然边界的长文本应保持完整，避免出现半句话气泡。"""
    formatter = QqReplyFormatter()
    message = "明天晚上如果有空我们一起开黑顺便把上次没打完的那局继续打完然后再聊聊后面的安排"

    assert formatter.format(message, "no-boundary", main_console_mode=True) == [message]
