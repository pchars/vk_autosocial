from api.vk import VkFloodError

# VK method -> (what the bot was doing, what stops working in the bot while VK blocks it).
ACTIONS = {
    "photos.getWallUploadServer": ("загрузку картинок в посты", "Пополнить отложку пока не получится."),
    "photos.saveWallPhoto": ("загрузку картинок в посты", "Пополнить отложку пока не получится."),
    "wall.post": ("публикацию постов", "Пополнить отложку пока не получится."),
    "wall.get": (
        "чтение стен групп",
        "Пока не получится собирать картинки из источников, смотреть отложку и аналитику.",
    ),
    "wall.delete": ("удаление постов", "Очистить отложку пока не получится."),
    "wall.repost": ("репосты", "Сделать репосты пока не получится."),
    "stories.getPhotoUploadServer": ("публикацию сторис", "Опубликовать сторис пока не получится."),
    "stories.save": ("публикацию сторис", "Опубликовать сторис пока не получится."),
    "groups.getMembers": ("чтение участников групп", "Аналитика пока не получится."),
    "groups.getById": (
        "получение данных о группах",
        "Пока не получится добавлять группы, источники и смотреть аналитику.",
    ),
    "users.get": ("проверку аккаунта ВК", ""),
    "account.getAppPermissions": ("проверку прав аккаунта ВК", ""),
}
DEFAULT_ACTION = ("запросы бота к ВК", "")

REASONS = {
    9: "слишком много однотипных действий подряд",
    14: "ВК попросил капчу, а бот не умеет её вводить",
    29: "исчерпан суточный лимит ВК на это действие",
}


def vk_action(method: str) -> str:
    return ACTIONS.get(method, DEFAULT_ACTION)[0]


def duration(seconds: int) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} мин"
    hours, rest = divmod(minutes, 60)
    return f"{hours} ч" + (f" {rest} мин" if rest else "")


def flood_text(exc: VkFloodError, by_bot: bool = False) -> str:
    action, blocked = ACTIONS.get(exc.method, DEFAULT_ACTION)
    reason = REASONS.get(exc.code, "слишком много действий")
    lines = [f"ВК временно ограничил {action}: {reason}."]
    if blocked:
        lines.append(blocked + " Остальные функции бота работают как обычно.")
    if exc.retry_after:
        when = duration(exc.retry_after)
        lines.append(f"Попробую снова сам примерно через {when}." if by_bot
                     else f"Можно будет снова примерно через {when}.")
    else:
        lines.append("Попробую снова позже." if by_bot else "Попробуйте позже.")
    return " ".join(lines)
