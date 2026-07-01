"""
Task Tracker - Handlers
"""
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.filters.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from config import BOT_TOKEN, ADMIN_USER_IDS
from db import init_db, create_task, get_all_tasks, update_task_status, delete_task, update_task, TaskStatus, TaskPriority
from ai_service import analyze_message, parse_priority

router = Router()

logger = logging.getLogger(__name__)

ITEMS_PER_PAGE = 8


class EditStates(StatesGroup):
    editing = State()


STATUS_ICONS = {
    TaskStatus.NEW: "🆕",
    TaskStatus.IN_PROGRESS: "▶️",
    TaskStatus.DONE: "✅",
    TaskStatus.CANCELLED: "❌"
}


def get_active_tasks():
    """Get all non-closed tasks (new + in_progress)."""
    all_tasks = get_all_tasks()
    return [t for t in all_tasks if t.status in (TaskStatus.NEW, TaskStatus.IN_PROGRESS)]


def make_pagination_keyboard(tasks, current_page, list_type="active"):
    """Create pagination keyboard for task list."""
    keyboard = []
    row = []

    start_idx = current_page * ITEMS_PER_PAGE
    end_idx = min(start_idx + ITEMS_PER_PAGE, len(tasks))

    for i in range(start_idx, end_idx):
        task = tasks[i]
        row.append(InlineKeyboardButton(text=str(task.id), callback_data=f"task_{task.id}"))
        if len(row) == 8:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    nav_row = []
    if current_page > 0:
        nav_row.append(InlineKeyboardButton(text="⏪ Назад", callback_data=f"page_{current_page - 1}_{list_type}"))
    if end_idx < len(tasks):
        nav_row.append(InlineKeyboardButton(text="Вперед ⏩", callback_data=f"page_{current_page + 1}_{list_type}"))
    if nav_row:
        keyboard.append(nav_row)

    if list_type == "active":
        keyboard.append([InlineKeyboardButton(text="✅ Завершенные", callback_data="completed_list")])
    else:
        keyboard.append([InlineKeyboardButton(text="📋 Активные", callback_data="active_list")])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def make_task_keyboard(task):
    """Create keyboard for task detail screen."""
    keyboard = []

    if task.status == TaskStatus.NEW:
        keyboard.append([
            InlineKeyboardButton(text="▶️ Начать", callback_data=f"start_{task.id}"),
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_{task.id}"),
        ])
        keyboard.append([
            InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{task.id}"),
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_{task.id}"),
        ])

    elif task.status == TaskStatus.IN_PROGRESS:
        keyboard.append([
            InlineKeyboardButton(text="⏸️ Отложить", callback_data=f"pause_{task.id}"),
            InlineKeyboardButton(text="✅ Завершить", callback_data=f"done_{task.id}"),
        ])
        keyboard.append([
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_{task.id}"),
            InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{task.id}"),
        ])
        keyboard.append([
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_{task.id}"),
        ])

    elif task.status == TaskStatus.DONE:
        keyboard.append([
            InlineKeyboardButton(text="🔄 Переоткрыть", callback_data=f"reopen_{task.id}"),
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_{task.id}"),
        ])
        keyboard.append([
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_{task.id}"),
            InlineKeyboardButton(text="✅ Завершенные", callback_data="completed_list"),
        ])

    else:
        keyboard.append([
            InlineKeyboardButton(text="🔄 Переоткрыть", callback_data=f"reopen_{task.id}"),
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_{task.id}"),
        ])

    if task.status == TaskStatus.DONE:
        keyboard.append([InlineKeyboardButton(text="📋 Список задач", callback_data="completed_list")])
    else:
        keyboard.append([InlineKeyboardButton(text="📋 Список задач", callback_data="active_list")])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def format_task_list_text(tasks, title, current_page, list_type="active"):
    """Format task list with pagination info and dates."""
    if not tasks:
        return f"{title}\n\n📭 Нет задач"

    start_idx = current_page * ITEMS_PER_PAGE
    end_idx = min(start_idx + ITEMS_PER_PAGE, len(tasks))
    page_tasks = tasks[start_idx:end_idx]

    text = f"{title}\n\n"
    for task in page_tasks:
        if list_type == "completed":
            text += f"{task.id}. {task.title}\n"
        else:
            icon = STATUS_ICONS.get(task.status, "📋")
            text += f"{icon} {task.id}. {task.title}\n"

    if len(tasks) > ITEMS_PER_PAGE:
        text += f"\n📄 {start_idx + 1}-{end_idx} из {len(tasks)}"
    return text.strip()


def format_task_text(task):
    """Format single task for display."""
    icon = STATUS_ICONS.get(task.status, "📋")
    date_str = task.created_at.strftime("%d.%m %Y")

    if task.status == TaskStatus.DONE:
        end_date = task.updated_at.strftime("%d.%m %Y")
        text = f"📅 {date_str} — {end_date}\n{icon} <b>{task.title}</b>\n\n"
    else:
        text = f"📅 {date_str}\n{icon} <b>{task.title}</b>\n\n"

    if task.description:
        text += f"{task.description}\n\n"

    return text.strip()


def is_admin(message_or_callback):
    """Check if user is admin."""
    if isinstance(message_or_callback, Message):
        return message_or_callback.from_user.id in ADMIN_USER_IDS
    elif isinstance(message_or_callback, CallbackQuery):
        return message_or_callback.from_user.id in ADMIN_USER_IDS
    return False


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Handle /start command."""
    if not is_admin(message):
        await message.answer("⛔ Доступ запрещён")
        return
    await message.answer(
        "📋 <b>Task Tracker</b>\n\n"
        "Отправь мне задачу текстом — я её проанализирую и добавлю.\n\n"
        "<b>Команды:</b>\n"
        "/list — все активные задачи\n"
        "/done — завершённые задачи"
    )
    await state.set_state(default_state)


@router.message(Command("list"))
async def cmd_list(message: Message, state: FSMContext = None):
    """Handle /list command - show all active tasks."""
    if not is_admin(message):
        await message.answer("⛔ Доступ запрещён")
        return
    tasks = get_active_tasks()
    keyboard = make_pagination_keyboard(tasks, 0, list_type="active")
    text = format_task_list_text(tasks, "📋 Активные задачи", 0, list_type="active")

    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(Command("done"))
async def cmd_done_list(message: Message, state: FSMContext = None):
    """Handle /done command - show completed tasks."""
    if not is_admin(message):
        await message.answer("⛔ Доступ запрещён")
        return
    tasks = get_all_tasks(status="done")
    keyboard = make_pagination_keyboard(tasks, 0, list_type="completed")
    text = format_task_list_text(tasks, "✅ Завершенные задачи", 0, list_type="completed")

    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "active_list")
async def callback_active_list(callback: CallbackQuery, state: FSMContext):
    """Show active tasks list."""
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    tasks = get_active_tasks()
    keyboard = make_pagination_keyboard(tasks, 0, list_type="active")
    text = format_task_list_text(tasks, "📋 Активные задачи", 0, list_type="active")

    await callback.message.delete()
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "completed_list")
async def callback_completed_list(callback: CallbackQuery, state: FSMContext):
    """Show completed tasks list."""
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    tasks = get_all_tasks(status="done")
    keyboard = make_pagination_keyboard(tasks, 0, list_type="completed")
    text = format_task_list_text(tasks, "✅ Завершенные задачи", 0, list_type="completed")

    await callback.message.delete()
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("page_"))
async def callback_page(callback: CallbackQuery, state: FSMContext):
    """Handle pagination."""
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    parts = callback.data.split("_")
    page = int(parts[1])
    list_type = parts[2] if len(parts) > 2 else "active"

    if list_type == "completed":
        tasks = get_all_tasks(status="done")
    else:
        tasks = get_active_tasks()

    keyboard = make_pagination_keyboard(tasks, page, list_type=list_type)
    title = "✅ Завершенные задачи" if list_type == "completed" else "📋 Активные задачи"
    text = format_task_list_text(tasks, title, page, list_type=list_type)

    await callback.message.delete()
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("task_"))
async def callback_task(callback: CallbackQuery, state: FSMContext):
    """Show task detail."""
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    task_id = int(callback.data.split("_")[1])
    tasks = get_all_tasks()
    task = next((t for t in tasks if t.id == task_id), None)

    if not task:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    keyboard = make_task_keyboard(task)
    text = format_task_text(task)

    await callback.message.delete()
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("start_"))
async def callback_start(callback: CallbackQuery, state: FSMContext):
    """Start task."""
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    task_id = int(callback.data.split("_")[1])
    update_task_status(task_id, "in_progress")

    tasks = get_active_tasks()
    keyboard = make_pagination_keyboard(tasks, 0, list_type="active")
    text = format_task_list_text(tasks, "📋 Активные задачи", 0, list_type="active")

    await callback.message.delete()
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("pause_"))
async def callback_pause(callback: CallbackQuery, state: FSMContext):
    """Pause task."""
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    task_id = int(callback.data.split("_")[1])
    update_task_status(task_id, "new")

    tasks = get_active_tasks()
    keyboard = make_pagination_keyboard(tasks, 0, list_type="active")
    text = format_task_list_text(tasks, "📋 Активные задачи", 0, list_type="active")

    await callback.message.delete()
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("done_"))
async def callback_done(callback: CallbackQuery, state: FSMContext):
    """Complete task."""
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    task_id = int(callback.data.split("_")[1])
    update_task_status(task_id, "done")

    tasks = get_all_tasks(status="done")
    keyboard = make_pagination_keyboard(tasks, 0, list_type="completed")
    text = format_task_list_text(tasks, "✅ Завершенные задачи", 0, list_type="completed")

    await callback.message.delete()
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("cancel_"))
async def callback_cancel(callback: CallbackQuery, state: FSMContext):
    """Cancel task."""
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    task_id = int(callback.data.split("_")[1])
    update_task_status(task_id, "cancelled")

    await callback.answer("Задача отменена", show_alert=True)

    tasks = get_active_tasks()
    keyboard = make_pagination_keyboard(tasks, 0, list_type="active")
    text = format_task_list_text(tasks, "📋 Активные задачи", 0, list_type="active")

    await callback.message.delete()
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("reopen_"))
async def callback_reopen(callback: CallbackQuery, state: FSMContext):
    """Reopen task."""
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    task_id = int(callback.data.split("_")[1])
    update_task_status(task_id, "new")

    await callback.answer("Задача переоткрыта", show_alert=True)

    tasks = get_active_tasks()
    keyboard = make_pagination_keyboard(tasks, 0, list_type="active")
    text = format_task_list_text(tasks, "📋 Активные задачи", 0, list_type="active")

    await callback.message.delete()
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("delete_"))
async def callback_delete(callback: CallbackQuery, state: FSMContext):
    """Delete task."""
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    task_id = int(callback.data.split("_")[1])
    delete_task(task_id)

    await callback.answer("Задача удалена", show_alert=True)

    tasks = get_active_tasks()
    keyboard = make_pagination_keyboard(tasks, 0, list_type="active")
    text = format_task_list_text(tasks, "📋 Активные задачи", 0, list_type="active")

    await callback.message.delete()
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("edit_"))
async def callback_edit(callback: CallbackQuery, state: FSMContext):
    """Show edit screen."""
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    task_id = int(callback.data.split("_")[1])
    tasks = get_all_tasks()
    task = next((t for t in tasks if t.id == task_id), None)

    if not task:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    await state.update_data(edit_task_id=task_id, edit_origin_list=task.status.value)

    text = f"<b>{task.title}</b>\n\n"
    if task.description:
        text += f"{task.description}"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"back_from_edit_{task.status.value}")]
    ])

    await callback.message.delete()
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(EditStates.editing)


@router.callback_query(F.data.startswith("back_from_edit_"))
async def callback_back_from_edit(callback: CallbackQuery, state: FSMContext):
    """Go back from edit screen."""
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    status = callback.data.split("_")[-1]

    if status == "done":
        tasks = get_all_tasks(status="done")
        keyboard = make_pagination_keyboard(tasks, 0, list_type="completed")
        text = format_task_list_text(tasks, "✅ Завершенные задачи", 0, list_type="completed")
    else:
        tasks = get_active_tasks()
        keyboard = make_pagination_keyboard(tasks, 0, list_type="active")
        text = format_task_list_text(tasks, "📋 Активные задачи", 0, list_type="active")

    await callback.message.delete()
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(EditStates.editing)
async def process_edit(message: Message, state: FSMContext):
    """Process edited task text."""
    if not is_admin(message):
        await message.answer("⛔ Доступ запрещён")
        return
    data = await state.get_data()
    task_id = data.get("edit_task_id")

    text = message.text.strip()
    if "\n" in text:
        parts = text.split("\n", 1)
        title = parts[0].strip()
        description = parts[1].strip() if len(parts) > 1 else ""
    else:
        title = text
        description = ""

    update_task(task_id, title, description)

    tasks = get_all_tasks()
    task = next((t for t in tasks if t.id == task_id), None)

    if task:
        keyboard = make_task_keyboard(task)
        text = format_task_text(task)
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await message.answer("Задача не найдена")

    await state.set_state(default_state)


@router.message()
async def handle_any_message(message: Message, state: FSMContext):
    """Handle any message - create new task."""
    if not is_admin(message):
        await message.answer("⛔ Доступ запрещён")
        return
    await message.answer("🔄 Анализирую...")

    task_data = await analyze_message(message.text)

    if task_data:
        title = task_data.get("title", message.text[:100])
        description = task_data.get("description", "")
        priority = parse_priority(task_data.get("priority", "medium"))

        task_id = create_task(
            title=title,
            description=description,
            priority=priority,
            created_by="Андрей",
            raw_message=message.text
        )

        status_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}
        emoji = status_emoji.get(priority, "🟡")

        await message.answer(
            f"✅ <b>Задача #{task_id} создана!</b>\n\n"
            f"{emoji} <b>{title}</b>\n"
            f"📝 {description}",
            parse_mode="HTML"
        )
    else:
        task_id = create_task(
            title=message.text[:100],
            description="",
            priority="medium",
            created_by="Андрей",
            raw_message=message.text
        )
        await message.answer(
            f"✅ <b>Задача #{task_id} создана!</b>\n\n"
            f"📋 {message.text[:100]}...",
            parse_mode="HTML"
        )