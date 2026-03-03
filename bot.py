import json
import asyncio
import random
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, FSInputFile
)

# ----------------- Логи -----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------- ТОКЕН -----------------
TOKEN = os.getenv("TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ----------------- Пути -----------------
BASE_DIR = Path(__file__).parent
TASKS_DIR = BASE_DIR / "data" / "tasks"
PROGRESS_FILE = BASE_DIR / "progress.json"

# ----------------- Хранилище -----------------
user_progress = {}
progress_lock = asyncio.Lock()

# ----------------- Загрузка / сохранение -----------------
def load_progress():
    global user_progress
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                user_progress = json.load(f)
        except:
            user_progress = {}
    else:
        user_progress = {}

async def save_progress():
    async with progress_lock:
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(user_progress, f, ensure_ascii=False, indent=2)

def ukey(uid: int) -> str:
    return str(uid)

# ----------------- Файлы -----------------
def get_task_images(task_num: int):
    folder = TASKS_DIR / str(task_num)
    if not folder.exists():
        return []
    return sorted(
        list(folder.glob("*.png")) +
        list(folder.glob("*.jpg")) +
        list(folder.glob("*.jpeg"))
    )

def get_answers(task_num: int):
    path = TASKS_DIR / str(task_num) / "answers.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def build_order(task_num: int):
    imgs = get_task_images(task_num)
    order = [{"image": str(i), "key": i.stem, "proto": i.stem} for i in imgs]
    random.shuffle(order)
    return order

# ----------------- Главное меню -----------------
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Выбрать задание")],
        [KeyboardButton(text="🎓 Мини экзамен")],
        [KeyboardButton(text="📊 Личный кабинет")],
    ],
    resize_keyboard=True
)

# ----------------- /start -----------------
@dp.message(Command("start"))
async def start(message: Message):
    load_progress()
    await message.answer("Привет 👋 Это бот для подготовки к ЕГЭ по профильной математике.", reply_markup=main_menu)

# ----------------- Выбор задания -----------------
@dp.message(F.text == "📝 Выбрать задание")
async def choose_task(message: Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=str(i), callback_data=f"task:{i}")]
            for i in range(1, 13)
        ]
    )
    await message.answer("Выбери номер задания:", reply_markup=kb)

# ----------------- Показ задания -----------------
@dp.callback_query(F.data.startswith("task:"))
async def show_task(callback: CallbackQuery):
    task_num = int(callback.data.split(":")[1])
    key = ukey(callback.from_user.id)

    order = build_order(task_num)
    if not order:
        await callback.message.answer("❌ В этом задании нет картинок.")
        return

    user_progress.setdefault(key, {})
    user = user_progress[key]

    user.update({
        "current_task": task_num,
        "order": order,
        "index": 0,
        "exam_mode": False
    })
    user.setdefault("history", [])
    user.setdefault("mistakes", [])

    await save_progress()
    await send_task(callback.message.chat.id, key)

# ----------------- Отправка задания -----------------
async def send_task(chat_id: int, key: str):
    user = user_progress.get(key)
    if not user:
        return

    order = user["order"]
    index = user["index"]

    if index >= len(order):
        if user.get("exam_mode"):
            hist = user["history"]
            correct = sum(1 for h in hist if h["correct"])
            await bot.send_message(
                chat_id,
                f"🎓 Мини экзамен завершён!\nРезультат: {correct}/12",
                reply_markup=main_menu
            )
        else:
            await bot.send_message(chat_id, "🎉 Задание завершено!", reply_markup=main_menu)
        return

    item = order[index]
    task_num = item.get("task", user["current_task"])

    await bot.send_photo(
        chat_id,
        FSInputFile(item["image"]),
        caption=f"📘 Задание {task_num}\nВведи ответ:"
    )

# ----------------- Проверка ответа -----------------
def parse_number(s: str):
    s = s.replace(",", ".")
    if "/" in s:
        a, b = s.split("/", 1)
        return float(a) / float(b)
    return float(s)

def answers_equal(a: str, b: str):
    try:
        return abs(parse_number(a) - parse_number(b)) < 1e-4
    except:
        return a.strip().lower() == b.strip().lower()

@dp.message(
    F.text &
    ~F.text.in_([
        "📝 Выбрать задание",
        "🎓 Мини экзамен",
        "📊 Личный кабинет",
        "⬅️ Главное меню",
        "📝 Прорешать ошибки"
    ])
)
async def check_answer(message: Message):
    key = ukey(message.from_user.id)
    user = user_progress.get(key)
    if not user:
        await message.answer("Сначала выбери задание.", reply_markup=main_menu)
        return

    order = user["order"]
    index = user["index"]
    item = order[index]

    task_num = item.get("task", user["current_task"])
    answers = get_answers(task_num)
    correct_answer = answers.get(item["key"], "")

    is_correct = answers_equal(message.text, str(correct_answer))

    user.setdefault("history", []).append({
        "task": task_num,
        "proto": item["proto"],
        "correct": is_correct
    })

    if is_correct:
        await message.answer("✅ Верно!")
    else:
        await message.answer(f"❌ Неверно\nПравильный ответ: {correct_answer}")

    user["index"] += 1
    await save_progress()
    await send_task(message.chat.id, key)

# ----------------- Личный кабинет -----------------
@dp.message(F.text == "📊 Личный кабинет")
async def profile(message: Message):
    key = ukey(message.from_user.id)
    hist = user_progress.get(key, {}).get("history", [])

    total = len(hist)
    correct = sum(1 for h in hist if h["correct"])
    acc = int(100 * correct / total) if total else 0

    await message.answer(
        f"📊 Личный кабинет:\n\n"
        f"Решено: {total}\n"
        f"Правильно: {correct}\n"
        f"Точность: {acc}%",
        reply_markup=main_menu
    )

# ----------------- Мини экзамен -----------------
@dp.message(F.text == "🎓 Мини экзамен")
async def mini_exam(message: Message):
    key = ukey(message.from_user.id)
    exam_order = []

    # строго 1→12, по одной картинке из каждого задания
    for task_num in range(1, 13):
        images = get_task_images(task_num)
        if not images:
            await message.answer(f"❌ Нет заданий для номера {task_num}")
            return
        img = random.choice(images)
        exam_order.append({
            "image": str(img),
            "key": img.stem,
            "proto": img.stem,
            "task": task_num
        })

    user_progress.setdefault(key, {})
    user = user_progress[key]

    user.update({
        "order": exam_order,
        "index": 0,
        "exam_mode": True,
        "current_task": 1,
        "history": []
    })

    await save_progress()
    await send_task(message.chat.id, key)

# ----------------- Запуск -----------------
async def main():
    load_progress()
    logger.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
