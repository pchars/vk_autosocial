from typing import List, Optional

from api.vk import VkAuthError, VkError, VkFloodError
from services.analytics import WEEKDAYS, Comparison, Recommendation, Report, comparison_rows, percent
from services.engine import FillResult, UserError
from services.vk_actions import flood_text, vk_action
from services.publisher import QueueInfo
from utils.dates import human_date, plural
from utils.store import Community, Source

MENU_GROUPS = "Мои группы"
MENU_ADD = "Добавить группу"
MENU_ACCOUNT = "Моя страница ВК"
MENU_HELP = "Помощь"
WELCOME = (
    "Привет! Я слежу, чтобы в ваших группах ВК всегда были запланированные посты.\n\n"
    "Как это работает:\n"
    "1. Вы добавляете свою группу.\n"
    "2. Присылаете ссылки на группы, откуда брать картинки.\n"
    "3. Дальше я сам держу отложку заполненной и пишу сюда, если что-то не так.\n\n"
    "Пользуйтесь кнопками внизу экрана."
)

HELP = (
    "Что умеют кнопки:\n\n"
    f"«{MENU_GROUPS}» — список групп. Нажмите на группу, чтобы увидеть, сколько постов запланировано, "
    "пополнить отложку, поменять источники и настройки, выложить сторис или посмотреть аналитику.\n\n"
    f"«{MENU_ADD}» — подключить новую группу. Понадобится ссылка на группу, где у вашего аккаунта ВК "
    "есть права редактора или администратора.\n\n"
    f"«{MENU_ACCOUNT}» — репосты на вашу стену и обновление доступа к ВК.\n\n"
    "Аналитика подскажет лучшее время для постов, его можно применить одной кнопкой. "
    "Чтобы посмотреть аналитику любой другой группы и сравнить её с вашей, просто пришлите ссылку на неё.\n\n"
    "Отложка пополняется сама примерно раз в полчаса, если в ней не хватает постов. "
    "Нажимать что-то каждый день не нужно.\n\n"
    "Если что-то пошло не так, я напишу сюда, что именно и что сделать."
)

ASK_GROUP_LINK = (
    "Пришлите ссылку на вашу группу ВК, куда нужно ставить посты.\n"
    "Например: https://vk.com/club123456\n\n"
    "Ссылку можно скопировать из адресной строки, открыв группу в браузере."
)
ASK_SOURCES = (
    "Пришлите ссылки на группы, откуда брать картинки. Лучше 3–5 штук, можно все одним сообщением, "
    "каждую с новой строки."
)
ASK_TOKEN = (
    "Чтобы обновить доступ к ВК:\n\n"
    "1. Нажмите кнопку «Открыть ВК» ниже и разрешите доступ.\n"
    "2. Откроется почти пустая страница. ВК предупредит, что адрес нельзя никому отправлять, "
    "но это ваш собственный бот, ему можно.\n"
    "3. Скопируйте адрес этой страницы целиком из адресной строки и пришлите сюда.\n\n"
    "Сообщение с адресом я сразу удалю из чата."
)
NOT_ADMIN = (
    "Это закрытый бот. Я отправил владельцу запрос на доступ — как только он подтвердит, "
    "я напишу. Ваш Telegram ID: {tg_id}"
)
CANCELLED = "Хорошо, отменил."
UNKNOWN_TEXT = "Не понял сообщение. Пользуйтесь кнопками внизу экрана или нажмите «Помощь»."

SETTING_TITLES = {
    "posts_per_day": "Постов в день",
    "queue_days": "Запас на сколько дней",
    "photos_per_post": "Картинок в одном посте",
    "chat_every": "Приглашение в чат — каждый N-й пост",
}
SETTING_OPTIONS = {
    "posts_per_day": [1, 2, 3, 4, 6, 8, 12],
    "queue_days": [7, 14, 30, 60, 90, 120],
    "photos_per_post": [1, 2, 3, 4, 6, 8, 10],
    "chat_every": [0, 3, 5, 6, 10, 20],
}
TEXT_FIELDS = {
    "hashtags": ("Хэштеги", "Пришлите хэштеги одной строкой, например: #цветы #весна\nЧтобы убрать — пришлите минус: -"),
    "chat_link": ("Ссылка на чат", "Пришлите ссылку на чат группы.\nЧтобы убрать — пришлите минус: -"),
    "chat_text": ("Текст приглашения", (
        "Пришлите текст приглашения в чат. Ссылку на чат я добавлю сам.\nЧтобы вернуть стандартный текст — пришлите минус: -"
    )),
}


def community_card(community: Community, sources: List[Source], stock: int,
                   queue: Optional[QueueInfo], tz: str) -> str:
    lines = [f"«{community.title}»", community.url, ""]
    if queue is None:
        lines.append("В отложке: не удалось узнать, ВК не ответил.")
    elif queue.count == 0:
        lines.append("В отложке: пусто.")
    else:
        lines.append(f"В отложке: {plural(queue.count, 'пост', 'поста', 'постов')}"
                     + (f", последний — {human_date(queue.last_date, tz)}." if queue.last_date else "."))
    if community.autofill:
        lines.append(
            f"Автопополнение включено: держу {plural(community.queue_target, 'пост', 'поста', 'постов')} "
            f"({community.posts_per_day} в день на {plural(community.queue_days, 'день', 'дня', 'дней')})."
        )
    else:
        lines.append("Автопополнение выключено — пополняйте кнопкой.")
    lines.append(f"Источников: {len(sources)}. Картинок в запасе: {stock}.")
    if not sources:
        lines.append("\nДобавьте источники, иначе брать картинки неоткуда.")
    return "\n".join(lines)


def sources_text(community: Community, sources: List[Source]) -> str:
    if not sources:
        return f"У «{community.title}» пока нет источников.\n\n{ASK_SOURCES}"
    lines = [f"Источники картинок для «{community.title}»:"]
    lines += [f"{i}. {s.title} — {s.url}" for i, s in enumerate(sources, 1)]
    lines.append("\nЧтобы убрать источник, нажмите на него ниже.")
    return "\n".join(lines)


def settings_text(community: Community) -> str:
    chat = "выключено"
    if community.chat_every and community.chat_link:
        chat = f"каждый {community.chat_every}-й пост"
    return "\n".join([
        f"Настройки «{community.title}»",
        "",
        f"Постов в день: {community.posts_per_day}",
        f"Время публикаций: {schedule_text(community)}",
        f"Запас: на {plural(community.queue_days, 'день', 'дня', 'дней')} "
        f"(это {plural(community.queue_target, 'пост', 'поста', 'постов')})",
        f"Картинок в посте: {community.photos_per_post}",
        f"Автопополнение: {'включено' if community.autofill else 'выключено'}",
        f"Подписи к постам: {'фразы из базы' if community.use_quotes else 'без подписи, только хэштеги'}",
        f"Хэштеги: {community.hashtags or 'нет'}",
        f"Чат: {community.chat_link or 'не указан'}",
        f"Приглашение в чат: {chat}",
    ])


def fill_result(community: Community, result: FillResult, tz: str) -> str:
    if result.needed == 0:
        return f"В «{community.title}» отложка уже полная: {plural(result.queued_before, 'пост', 'поста', 'постов')}."
    created = result.published.posts_created
    lines = [f"«{community.title}»: добавил в отложку {plural(created, 'пост', 'поста', 'постов')}."]
    if result.published.last_date:
        lines.append(f"Посты запланированы до {human_date(result.published.last_date, tz)}.")
    if result.published.denied:
        lines.append(
            "ВК не дал поставить больше постов. Обычно это суточный лимит ВК — продолжу позже сам. "
            "Если так будет и завтра, проверьте, что аккаунт — редактор или админ группы."
        )
    elif result.published.throttled:
        lines.append(
            "ВК начал притормаживать загрузку картинок, поэтому я остановился, чтобы не получить блокировку на час. "
            + ("Продолжу позже сам." if community.autofill else "Нажмите «Пополнить» ещё раз минут через 15–20.")
        )
    elif result.remaining and created:
        lines.append(
            f"Осталось добавить ещё {plural(result.remaining, 'пост', 'поста', 'постов')}"
            + (" — сделаю это сам в ближайшее время." if community.autofill else " — нажмите «Пополнить» ещё раз.")
        )
    elif created < result.needed:
        lines.append(f"Хотел добавить {result.needed}, но не хватило картинок.")
        if result.collected.failed_sources:
            lines.append("Не открылись источники: " + ", ".join(result.collected.failed_sources) + ".")
        lines.append("Добавьте ещё источников в карточке группы.")
    if result.published.upload_failed and not result.published.throttled:
        lines.append(f"ВК не принял {plural(result.published.upload_failed, 'картинку', 'картинки', 'картинок')}, пропустил их.")
    return "\n".join(lines)


def _number(value: float) -> str:
    return f"{value:,}".replace(",", " ")


def hours_text(hours: List[int]) -> str:
    return ", ".join(f"{h:02d}:00" for h in hours)


def days_text(days: List[int]) -> str:
    return "каждый день" if len(days) == 7 else ", ".join(WEEKDAYS[d] for d in days)


def schedule_text(community: Community) -> str:
    when = hours_text(community.hours) if community.hours else "равномерно в течение суток"
    return f"{when}, {days_text(community.days)}"


def analytics_text(report: Report, own: bool = True) -> str:
    m, p = report.members, report.posts
    lines = [f"Аналитика «{report.community.title}»"] + ([report.community.url] if report.community.url else [])
    lines += ["", f"Участников: {_number(m.total)}"]
    if m.hidden:
        lines.append("Список участников скрыт — про аудиторию ВК ничего не показывает.")
    elif m.sampled:
        if m.sampled < m.total:
            lines.append(f"Аудиторию считаю по выборке из {_number(m.sampled)} участников со всего списка.")
        lines.append(f"Удалённых и заблокированных страниц: {percent(m.dead, m.sampled)}%")
        if m.alive:
            lines.append(f"Женщин: {percent(m.female, m.alive)}%, мужчин: {percent(m.male, m.alive)}%")
            lines.append(f"Заходили за неделю: {percent(m.active_week, m.alive)}%, "
                         f"давно не заходили: {percent(m.inactive, m.alive)}%")
    lines.append("")
    if p.hidden:
        lines.append("Стена закрыта — посты посмотреть нельзя.")
    elif not p.count:
        lines.append("За последние 30 дней постов не было.")
    else:
        lines += [
            f"Постов за 30 дней: {p.count} (≈{p.per_day} в день)",
            "Обычный пост: " + ", ".join([
                plural(p.median_views, "просмотр", "просмотра", "просмотров"),
                plural(p.median_likes, "лайк", "лайка", "лайков"),
                plural(p.median_reposts, "репост", "репоста", "репостов"),
                plural(p.median_comments, "комментарий", "комментария", "комментариев"),
            ]),
            f"Охват поста: {report.reach}% участников",
            f"Вовлечённость: {p.engagement}% (лайки, репосты и комментарии от просмотров)",
        ]
    rec = report.recommendation
    if rec:
        lines += ["", recommendation_text(rec, own)]
    return "\n".join(lines)


def recommendation_text(rec: Recommendation, own: bool = True) -> str:
    basis = "когда заходит аудитория и как набирали просмотры посты" if rec.by_posts else "когда заходит аудитория"
    title = "Лучшее время для постов" if own else "Лучшее время для постов у этой группы"
    text = f"{title}: {hours_text(rec.hours)}, {days_text(rec.days)}.\nПосчитал по тому, {basis}."
    if len(rec.days) < 7:
        weak = [WEEKDAYS[d] for d in range(7) if d not in rec.days]
        text += f"\nВ {', '.join(weak)} посты набирают заметно меньше просмотров."
    return text


def schedule_question(community: Community, rec: Recommendation) -> str:
    return (
        f"Публиковать посты в «{community.title}» в {hours_text(rec.hours)}, {days_text(rec.days)}?\n\n"
        f"Это {plural(len(rec.hours), 'пост', 'поста', 'постов')} в день. Уже запланированные посты "
        "останутся как есть, новое время будет у следующих. Вернуть равномерное расписание можно в настройках."
    )


def comparison_text(comparison: Comparison) -> str:
    mine, other = comparison.mine, comparison.other
    lines = [f"Сравнение: «{mine.community.title}» и «{other.community.title}»", "",
             f"Участников: {_number(mine.members.total)} и {_number(other.members.total)}"]
    for title, a, b in comparison_rows(comparison):
        lines.append(f"{title}: {a} и {b}")
    if mine.posts.count and other.posts.count:
        lines.append(f"Обычный пост, просмотров: {_number(mine.posts.median_views)} и "
                     f"{_number(other.posts.median_views)}")
    if other.recommendation:
        lines += ["", f"У «{other.community.title}» лучшее время: {hours_text(other.recommendation.hours)}."]
    if mine.recommendation:
        lines.append(f"У «{mine.community.title}»: {hours_text(mine.recommendation.hours)}.")
    if other.members.hidden or other.posts.hidden:
        lines += ["", f"«{other.community.title}» скрывает часть данных, сравнил только то, что видно."]
    return "\n".join(lines)


def explain_error(exc: BaseException) -> str:
    if isinstance(exc, UserError):
        return str(exc)
    if isinstance(exc, VkAuthError):
        return ("ВК не пускает бота: доступ к аккаунту устарел или отозван. "
                f"Владелец бота может обновить его: «{MENU_ACCOUNT}» → «Обновить доступ к ВК».")
    if isinstance(exc, VkFloodError):
        return flood_text(exc)
    if isinstance(exc, VkError):
        action = vk_action(exc.method)
        if exc.code in (3, 23):
            return (f"ВК отключил {action} для сторонних приложений, через бота это больше не сделать. "
                    "Остальные функции работают как обычно.")
        if exc.code == 214:
            return ("ВК не дал опубликовать пост: либо исчерпан суточный лимит постов, "
                    "либо у аккаунта нет прав редактора в группе. Попробуйте завтра.")
        if exc.code in (15, 203, 7):
            return (f"ВК не разрешил {action}: у аккаунта не хватает прав в группе. "
                    "Проверьте, что аккаунт — админ или редактор этой группы.")
        if exc.code == -1:
            return "Не удалось связаться с ВК. Попробуйте ещё раз через пару минут."
        return f"ВК не выполнил {action} и ответил ошибкой: {exc.message}"
    return "Что-то пошло не так. Подробности записаны в лог, попробуйте ещё раз позже."
