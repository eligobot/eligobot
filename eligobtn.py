import os
import random
import logging
import asyncio
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters
)
from dotenv import load_dotenv
import pendulum

# --- Konfigurasi Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- State untuk ConversationHandler ---
CHOOSE_TYPE, GET_DURATION, GET_PRIZE_AUTO, GET_PRIZE_MANUAL = range(4)

# --- Konstanta ---
CALLBACK_JOIN = "join"
CALLBACK_PICK_WINNER = "pick"
MAX_WINNERS = 3

class GiveawayBot:
    def __init__(self, token: str):
        self.giveaways = defaultdict(lambda: defaultdict(dict))
        self.application = Application.builder().token(token).build()
        self._register_handlers()

    def _register_handlers(self):
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("eligo", self.start_eligo, filters=filters.ChatType.GROUPS | filters.ChatType.CHANNEL)],
            states={
                CHOOSE_TYPE: [CallbackQueryHandler(self.choose_type)],
                GET_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_duration)],
                GET_PRIZE_AUTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_prize_auto)],
                GET_PRIZE_MANUAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_prize_manual)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel, filters=filters.ChatType.GROUPS | filters.ChatType.CHANNEL)],
            conversation_timeout=120
        )
        self.application.add_handler(conv_handler)
        self.application.add_handler(CommandHandler("help", self.show_help, filters=filters.ChatType.GROUPS | filters.ChatType.CHANNEL))
        self.application.add_handler(CommandHandler("start", self.show_help, filters=filters.ChatType.GROUPS | filters.ChatType.CHANNEL))
        self.application.add_handler(CallbackQueryHandler(self.handle_button_press))

    async def _is_admin(self, chat_id: int, user_id: int) -> bool:
        admins = await self.application.bot.get_chat_administrators(chat_id)
        return any(admin.user.id == user_id for admin in admins)
    
    # --- Alur Conversation ---
    async def start_eligo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        user = update.effective_user
        sender_id = user.id if user else update.effective_sender.id
        if not await self._is_admin(update.effective_chat.id, sender_id):
            await update.message.reply_text("Hanya admin yang bisa memulai giveaway.")
            return ConversationHandler.END
        keyboard = [[
            InlineKeyboardButton("Otomatis ⏰", callback_data="auto"),
            InlineKeyboardButton("Manual ✋", callback_data="manual"),
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Hai! Silakan pilih tipe giveaway yang ingin dibuat:", reply_markup=reply_markup)
        return CHOOSE_TYPE

    async def choose_type(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        draw_type = query.data
        context.user_data['draw_type'] = draw_type
        if draw_type == 'auto':
            await query.edit_message_text("Tipe: Otomatis ⏰\n\nSekarang, masukkan durasi giveaway (contoh: 30m, 2h, 1d):")
            return GET_DURATION
        else:
            await query.edit_message_text("Tipe: Manual ✋\n\nSekarang, masukkan nama hadiahnya:")
            return GET_PRIZE_MANUAL

    async def get_duration(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        duration_str = update.message.text
        try:
            now = pendulum.now()
            num = int(duration_str[:-1])
            unit = duration_str[-1].lower()
            if unit == 's': end_time = now.add(seconds=num)
            elif unit == 'm': end_time = now.add(minutes=num)
            elif unit == 'h': end_time = now.add(hours=num)
            elif unit == 'd': end_time = now.add(days=num)
            else: raise ValueError("Unit tidak valid")
            duration_seconds = (end_time - now).total_seconds()
            if duration_seconds <= 0: raise ValueError("Durasi harus positif")
            context.user_data['duration_str'] = duration_str
            context.user_data['duration_seconds'] = duration_seconds
            await update.message.reply_text(f"Durasi: {duration_str}\n\nTerakhir, masukkan nama hadiahnya:")
            return GET_PRIZE_AUTO
        except Exception:
            await update.message.reply_text(f"Format waktu '{duration_str}' tidak valid. Coba lagi atau ketik /cancel.")
            return GET_DURATION

    async def get_prize_auto(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        deskripsi = update.message.text
        duration_str = context.user_data['duration_str']
        duration_seconds = context.user_data['duration_seconds']
        await self.create_giveaway_message(update, context, deskripsi, duration_str, duration_seconds)
        return ConversationHandler.END

    async def get_prize_manual(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        deskripsi = update.message.text
        await self.create_giveaway_message(update, context, deskripsi, "Manual")
        return ConversationHandler.END

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await update.message.reply_text("Pembuatan giveaway dibatalkan.")
        return ConversationHandler.END

    async def create_giveaway_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, deskripsi: str, duration_str: str, duration_seconds: int = None):
        info_waktu = f"**Akan diundi otomatis dalam:** {duration_str}" if duration_seconds is not None else "**Akan diundi manual oleh admin**"
        keyboard = [
            [InlineKeyboardButton("✅ Ikut Giveaway (0)", callback_data=f"{CALLBACK_JOIN}:{update.effective_chat.id}")],
            [InlineKeyboardButton("🏆 Pilih Pemenang (Manual)", callback_data=f"{CALLBACK_PICK_WINNER}:{update.effective_chat.id}")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.effective_chat.type == 'channel':
            pesan_giveaway = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"🎉 **GIVEAWAY DIMULAI!** 🎉\n\n**Hadiah:** {deskripsi}\n{info_waktu}\n\nKlik tombol di bawah untuk berpartisipasi!",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            try: await update.effective_message.delete()
            except: pass
        else:
            pesan_giveaway = await update.message.reply_text(
                f"🎉 **GIVEAWAY DIMULAI!** 🎉\n\n**Hadiah:** {deskripsi}\n{info_waktu}\n\nKlik tombol di bawah untuk berpartisipasi!",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
        
        chat_id = pesan_giveaway.chat_id
        message_id = pesan_giveaway.message_id
        admin_id = update.effective_user.id if update.effective_user else update.effective_sender.id
        self.giveaways[chat_id][message_id] = {'participants': set(), 'author': admin_id, 'task': None}
        if duration_seconds:
            auto_draw_task = asyncio.create_task(self.schedule_draw(duration_seconds, chat_id, message_id))
            self.giveaways[chat_id][message_id]['task'] = auto_draw_task
            logger.info(f"Giveaway Otomatis dimulai ({duration_str}) untuk pesan {message_id} oleh {admin_id}")
        else:
            logger.info(f"Giveaway Manual dimulai untuk pesan {message_id} oleh {admin_id}")

    async def handle_button_press(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        action_data = query.data.split(':')
        action = action_data[0]
        if action == CALLBACK_JOIN: await self._handle_join(query)
        elif action == CALLBACK_PICK_WINNER: await self._handle_manual_pick_winner(query)
        else: await query.answer()
    
    async def show_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        help_text = (
            "👋 **Selamat Datang di Eligobot!**\n\n"
            "Bot untuk membuat giveaway interaktif.\n\n"
            "▶️ **Cara Memulai (Hanya Admin)**\n"
            "1. Ketik `/eligo`.\n"
            "2. Pilih tipe giveaway: **Otomatis** (dengan timer) atau **Manual** (tanpa timer).\n"
            "3. Ikuti instruksi selanjutnya dari bot.\n\n"
            "⏹️ **Membatalkan**\n"
            "Ketik `/cancel` kapan saja selama proses pembuatan untuk batal."
        )
        await update.message.reply_text(help_text, parse_mode='HTML')

    async def schedule_draw(self, delay: int, chat_id: int, message_id: int):
        await asyncio.sleep(delay)
        await self._pick_winner_logic(chat_id, message_id)

    async def _handle_join(self, query: "CallbackQuery") -> None:
        user, chat_id, message_id = query.from_user, query.message.chat_id, query.message.message_id
        if message_id not in self.giveaways[chat_id]:
            await query.answer(text="Giveaway ini sudah berakhir.", show_alert=True)
            return
        participants = self.giveaways[chat_id][message_id]['participants']
        is_new_participant = user.id not in participants
        if is_new_participant:
            participants.add(user.id)
            await query.answer(text="Kamu berhasil bergabung!", show_alert=False)
            logger.info(f"User {user.first_name} bergabung ke giveaway {message_id}")
            participant_count = len(participants)
            new_keyboard = [
                [InlineKeyboardButton(f"✅ Ikut Giveaway ({participant_count})", callback_data=f"{CALLBACK_JOIN}:{chat_id}")],
                [InlineKeyboardButton("🏆 Pilih Pemenang (Manual)", callback_data=f"{CALLBACK_PICK_WINNER}:{chat_id}")],
            ]
            reply_markup = InlineKeyboardMarkup(new_keyboard)
            try:
                await query.edit_message_reply_markup(reply_markup=reply_markup)
            except Exception as e:
                logger.warning(f"Gagal mengupdate tombol: {e}")
        else:
            await query.answer(text="Kamu sudah terdaftar.", show_alert=False)

    async def _handle_manual_pick_winner(self, query: "CallbackQuery") -> None:
        user, chat_id, message_id = query.from_user, query.message.chat_id, query.message.message_id
        if not await self._is_admin(chat_id, user.id): await query.answer(text="Hanya admin yang bisa memilih pemenang.", show_alert=True); return
        if message_id not in self.giveaways[chat_id]: await query.answer(text="Giveaway ini sepertinya sudah berakhir.", show_alert=True); return
        task = self.giveaways[chat_id][message_id].get('task')
        if task: task.cancel()
        await self._pick_winner_logic(chat_id, message_id, original_message=query.message)
    
    async def _pick_winner_logic(self, chat_id: int, message_id: int, original_message=None):
        if message_id not in self.giveaways[chat_id]: return
        participants = list(self.giveaways[chat_id][message_id]['participants'])
        if not original_message:
            try: original_message = await self.application.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ Waktu habis! Mengundi pemenang...")
            except Exception as e:
                logger.error(f"Gagal mengedit pesan: {e}")
                if message_id in self.giveaways.get(chat_id, {}): del self.giveaways[chat_id][message_id]
                return
        original_text = original_message.text
        if not participants:
            await self.application.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"{original_text.split('---')[0]}\n\n--- Selesai ---\n\nTidak ada peserta.", reply_markup=None)
        else:
            num_to_pick = min(len(participants), MAX_WINNERS)
            winner_ids = random.sample(participants, k=num_to_pick)
            winner_tasks = [self.application.bot.get_chat_member(chat_id, w_id) for w_id in winner_ids]
            winner_infos = await asyncio.gather(*winner_tasks)
            peringkat_text = ""
            for i, winner_info in enumerate(winner_infos):
                rank_title = "🏆 Pemenang Utama" if i == 0 else f"🥈 Cadangan {i}"
                peringkat_text += f"{i+1}. {rank_title}: {winner_info.user.mention_html()}\n"
            await self.application.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"{original_text.split('---')[0]}\n\n--- Selesai ---\n\nHasilnya:\n\n{peringkat_text}\nSelamat!", parse_mode='HTML', reply_markup=None)
        if message_id in self.giveaways.get(chat_id, {}):
            del self.giveaways[chat_id][message_id]

    def run(self):
        logger.info("Bot mulai berjalan...")
        self.application.run_polling()

def main():
    load_dotenv()
    token = os.getenv("TELEGRAM_TOKEN")
    if not token: raise ValueError("Token tidak ditemukan!")
    bot = GiveawayBot(token)
    bot.run()

if __name__ == "__main__":
    main()