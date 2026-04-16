#!/usr/bin/env python3
"""
Telegram SaaS Bot - Premium Age Analysis Service
A full-featured Telegram bot with freemium model, user management, and analytics.
"""

import os
import logging
import sqlite3
import random
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from enum import Enum

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)
from telegram.constants import ChatAction
from telegram.request import HTTPXRequest

# ==================== Configuration ====================
# Set your bot token in environment variables or directly here
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8784599420:AAGJKQVqqccpfG3g3tRvqtoaVo9aE9DDAGM")

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
WAITING_FOR_AGE = 1

# Admin IDs (add your Telegram user ID here)
ADMIN_IDS = [123456789]  # Replace with actual admin IDs

# ==================== Enums and Constants ====================
class PlanType(str, Enum):
    FREE = "free"
    PREMIUM = "premium"

class Database:
    """SQLite database handler for user management."""
    
    def __init__(self, db_path: str = "users.db"):
        self.db_path = db_path
        self.init_database()
    
    def get_connection(self):
        """Create a database connection."""
        return sqlite3.connect(self.db_path, check_same_thread=False)
    
    def init_database(self):
        """Initialize database tables if they don't exist."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    plan_type TEXT DEFAULT 'free',
                    usage_count INTEGER DEFAULT 0,
                    last_usage_date TEXT,
                    joined_date TEXT DEFAULT CURRENT_TIMESTAMP,
                    total_scans INTEGER DEFAULT 0
                )
            """)
            
            # Reset daily usage for new day
            cursor.execute("""
                UPDATE users 
                SET usage_count = 0 
                WHERE date(last_usage_date) < date('now')
            """)
            
            conn.commit()
            logger.info("Database initialized successfully")
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        """Get user data from database."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,)
            )
            row = cursor.fetchone()
            
            if row:
                return {
                    "user_id": row[0],
                    "username": row[1],
                    "first_name": row[2],
                    "plan_type": row[3],
                    "usage_count": row[4],
                    "last_usage_date": row[5],
                    "joined_date": row[6],
                    "total_scans": row[7]
                }
            return None
    
    def create_or_update_user(self, user_id: int, username: str, first_name: str) -> Dict:
        """Create new user or update existing user info."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if user exists
            user = self.get_user(user_id)
            
            if not user:
                cursor.execute("""
                    INSERT INTO users (user_id, username, first_name, plan_type, usage_count)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, username, first_name, PlanType.FREE.value, 0))
                conn.commit()
                return self.get_user(user_id)
            
            # Update user info if changed
            if user["username"] != username or user["first_name"] != first_name:
                cursor.execute("""
                    UPDATE users 
                    SET username = ?, first_name = ? 
                    WHERE user_id = ?
                """, (username, first_name, user_id))
                conn.commit()
            
            return user
    
    def increment_usage(self, user_id: int) -> bool:
        """Increment usage count for user. Returns True if successful."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Reset usage if new day
            cursor.execute("""
                UPDATE users 
                SET usage_count = 0 
                WHERE user_id = ? AND date(last_usage_date) < date('now')
            """, (user_id,))
            
            # Increment usage
            cursor.execute("""
                UPDATE users 
                SET usage_count = usage_count + 1,
                    last_usage_date = datetime('now'),
                    total_scans = total_scans + 1
                WHERE user_id = ?
            """, (user_id,))
            
            conn.commit()
            return True
    
    def get_remaining_free_scans(self, user_id: int) -> int:
        """Get remaining free scans for today."""
        user = self.get_user(user_id)
        if not user:
            return 3
        
        if user["plan_type"] == PlanType.PREMIUM.value:
            return float('inf')  # Unlimited for premium
        
        # Check if it's a new day
        if user["last_usage_date"]:
            last_date = datetime.fromisoformat(user["last_usage_date"]).date()
            if last_date < datetime.now().date():
                return 3
        
        return max(0, 3 - user["usage_count"])
    
    def upgrade_to_premium(self, user_id: int) -> bool:
        """Upgrade user to premium plan."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users 
                SET plan_type = ? 
                WHERE user_id = ?
            """, (PlanType.PREMIUM.value, user_id))
            conn.commit()
            return cursor.rowcount > 0
    
    def get_stats(self) -> Dict:
        """Get bot statistics."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Total users
            cursor.execute("SELECT COUNT(*) FROM users")
            total_users = cursor.fetchone()[0]
            
            # Premium users
            cursor.execute("SELECT COUNT(*) FROM users WHERE plan_type = ?", (PlanType.PREMIUM.value,))
            premium_users = cursor.fetchone()[0]
            
            # Total scans
            cursor.execute("SELECT SUM(total_scans) FROM users")
            total_scans = cursor.fetchone()[0] or 0
            
            # Active users today
            cursor.execute("""
                SELECT COUNT(*) FROM users 
                WHERE date(last_usage_date) = date('now')
            """)
            active_today = cursor.fetchone()[0]
            
            return {
                "total_users": total_users,
                "premium_users": premium_users,
                "total_scans": total_scans,
                "active_today": active_today
            }

# ==================== Helper Functions ====================
def calculate_age_details(age: int) -> Dict:
    """Calculate various age-related details."""
    current_year = datetime.now().year
    birth_year = current_year - age
    
    # Calculate days lived (approximate)
    days_lived = age * 365 + (age // 4)  # Accounting for leap years
    
    # Calculate hours and minutes for fun
    hours_lived = days_lived * 24
    minutes_lived = hours_lived * 60
    
    return {
        "birth_year": birth_year,
        "days_lived": days_lived,
        "hours_lived": hours_lived,
        "minutes_lived": minutes_lived
    }

def generate_personality_insight(age: int, is_premium: bool = False) -> str:
    """Generate personality insights based on age."""
    insights = {
        "young": [
            "🌟 You're in your prime discovery years!",
            "🚀 The world is your oyster - keep exploring!",
            "💫 Your energy and enthusiasm are your superpowers!"
        ],
        "adult": [
            "🎯 You're building wisdom with every experience!",
            "⭐ Your perspective is uniquely valuable!",
            "🌈 Life's complexity makes you more interesting!"
        ],
        "mature": [
            "🏆 You've gathered a treasure trove of life experiences!",
            "📚 Your wisdom is a gift to those around you!",
            "✨ You've mastered the art of authentic living!"
        ]
    }
    
    if age < 30:
        category = "young"
    elif age < 50:
        category = "adult"
    else:
        category = "mature"
    
    base_insight = random.choice(insights[category])
    
    if is_premium:
        premium_addons = [
            "\n\n🔮 *Premium Deep Insight:* Your soul age is ancient and wise!",
            "\n\n🎭 *Premium Personality Profile:* You're a rare blend of thinker and doer!",
            "\n\n💎 *Premium Revelation:* You have an exceptional capacity for growth!"
        ]
        base_insight += random.choice(premium_addons)
    
    return base_insight

def generate_lucky_number() -> Tuple[int, str]:
    """Generate a lucky number with meaning."""
    lucky_num = random.randint(1, 99)
    
    meanings = {
        range(1, 10): "New beginnings await you!",
        range(10, 20): "Trust your intuition!",
        range(20, 30): "Balance is key to success!",
        range(30, 40): "Creativity will guide you!",
        range(40, 50): "Stability brings opportunities!",
        range(50, 60): "Change leads to growth!",
        range(60, 70): "Wisdom comes from experience!",
        range(70, 80): "Spiritual growth is happening!",
        range(80, 90): "Abundance flows your way!",
        range(90, 100): "Completion and new cycles!"
    }
    
    for num_range, meaning in meanings.items():
        if lucky_num in num_range:
            return lucky_num, meaning
    
    return lucky_num, "You're destined for greatness!"

def generate_future_prediction() -> str:
    """Generate a fun future prediction."""
    predictions = [
        "🌟 A wonderful opportunity will knock on your door within 3 months!",
        "💫 You'll discover a hidden talent that changes your path!",
        "🎯 An important person from your past will reappear with good news!",
        "🚀 A bold decision will lead to unexpected success!",
        "💎 Financial abundance is heading your way - stay prepared!",
        "🌈 You'll travel somewhere that transforms your perspective!",
        "⭐ Someone you help today will become important in your future!",
        "🌙 A creative project will bring you unexpected recognition!",
        "✨ You're about to enter a period of rapid personal growth!",
        "🎨 A new hobby will open doors you never imagined!"
    ]
    return random.choice(predictions)

def format_age_response(age: int, is_premium: bool = False, user_data: Dict = None) -> str:
    """Format the age analysis response."""
    details = calculate_age_details(age)
    personality = generate_personality_insight(age, is_premium)
    
    response = f"""
🎂 *Age Analysis Complete!*

📊 *Your Stats:*
• Age: {age} years young
• Born: ~{details['birth_year']}
• Days lived: {details['days_lived']:,} days
• Hours experienced: {details['hours_lived']:,} hours
• Minutes of life: {details['minutes_lived']:,} minutes

🎭 *Personality Insight:*
{personality}
"""
    
    if is_premium:
        lucky_num, meaning = generate_lucky_number()
        prediction = generate_future_prediction()
        
        response += f"""

💎 *PREMIUM INSIGHTS* 💎

🍀 *Lucky Number:* {lucky_num}
📖 *Meaning:* {meaning}

🔮 *Future Glimpse:*
{prediction}

✨ *Deep Wisdom:* You're exactly where you need to be in your life journey. Trust the process!

━━━━━━━━━━━━━━━━━━━━━
🌟 *Premium Member Benefits Active* 🌟
"""
    else:
        # Check remaining scans
        if user_data:
            remaining = 3 - user_data['usage_count']
            response += f"""

━━━━━━━━━━━━━━━━━━━━━
⚠️ *Free Plan Limit:* {remaining} scan{'s' if remaining != 1 else ''} remaining today

💎 *Upgrade to Premium* for:
• Deep personality insights
• Lucky numbers & meanings
• Future predictions
• Unlimited daily scans!
"""
    
    return response

def get_main_keyboard(is_premium: bool = False) -> ReplyKeyboardMarkup:
    """Get the main reply keyboard markup."""
    premium_emoji = "👑 " if is_premium else ""
    buttons = [
        [KeyboardButton("🎂 Check Age"), KeyboardButton(f"{premium_emoji}Premium")],
        [KeyboardButton("👤 My Account")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# ==================== Bot Handlers ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message when the command /start is issued."""
    user = update.effective_user
    
    # Store/update user in database
    db = Database()
    user_data = db.create_or_update_user(
        user.id,
        user.username or "",
        user.first_name
    )
    
    is_premium = user_data["plan_type"] == PlanType.PREMIUM.value
    
    welcome_text = f"""
✨ *Welcome to AgeSight Pro!* ✨

Hey {user.first_name}! 👋 I'm your personal age & personality analyst.

🎯 *What I Do:*
• Calculate your life statistics
• Reveal personality insights
• Provide premium predictions

{'👑 *You have PREMIUM access!* All features unlocked!' if is_premium else '🎁 *Free Plan:* 3 scans/day • Upgrade anytime!'}

Ready to discover insights about yourself? Choose an option below! 👇
"""
    
    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_keyboard(is_premium)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button presses from reply keyboard."""
    text = update.message.text
    user = update.effective_user
    
    db = Database()
    user_data = db.get_user(user.id)
    
    if not user_data:
        await update.message.reply_text("Please use /start first!")
        return
    
    if "Check Age" in text:
        # Check remaining scans for free users
        if user_data["plan_type"] == PlanType.FREE.value:
            remaining = db.get_remaining_free_scans(user.id)
            if remaining <= 0:
                await update.message.reply_text(
                    "⚠️ *Daily Limit Reached!*\n\n"
                    "You've used all 3 free scans for today.\n\n"
                    "💎 *Upgrade to Premium* for unlimited scans and exclusive insights!\n"
                    "Use /upgrade to see premium benefits.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
        
        await update.message.reply_text(
            "🎂 *Let's analyze your age!*\n\n"
            "Please enter your age in years (e.g., 25):",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data["awaiting_age"] = True
        return WAITING_FOR_AGE
    
    elif "Premium" in text:
        await premium_info(update, context)
    
    elif "My Account" in text:
        await account_info(update, context)

async def handle_age_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle age input from user."""
    user = update.effective_user
    text = update.message.text.strip()
    
    # Validate age input
    try:
        age = int(text)
        if age < 0 or age > 120:
            raise ValueError("Age out of realistic range")
    except ValueError:
        await update.message.reply_text(
            "❌ *Invalid age!*\n\n"
            "Please enter a valid age between 0 and 120 years.\n"
            "Example: 25",
            parse_mode=ParseMode.MARKDOWN
        )
        return WAITING_FOR_AGE
    
    # Show typing indicator
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    
    # Get user data and check premium status
    db = Database()
    user_data = db.get_user(user.id)
    is_premium = user_data["plan_type"] == PlanType.PREMIUM.value
    
    # Increment usage for free users
    if not is_premium:
        db.increment_usage(user.id)
        user_data = db.get_user(user.id)  # Refresh data
    
    # Generate and send response
    response = format_age_response(age, is_premium, user_data)
    
    await update.message.reply_text(
        response,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_keyboard(is_premium)
    )
    
    # Clear waiting state
    context.user_data.pop("awaiting_age", None)
    
    return ConversationHandler.END

async def premium_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show premium subscription information."""
    user = update.effective_user
    db = Database()
    user_data = db.get_user(user.id)
    
    if user_data["plan_type"] == PlanType.PREMIUM.value:
        await update.message.reply_text(
            "👑 *You're a Premium Member!* 👑\n\n"
            "✨ *Your Benefits:*\n"
            "• Unlimited daily scans\n"
            "• Deep personality insights\n"
            "• Lucky numbers with meanings\n"
            "• Future predictions\n"
            "• Priority support\n\n"
            "Thank you for being a valued member! 🌟",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_keyboard(True)
        )
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 Upgrade to Premium ($4.99/month)", callback_data="upgrade_premium")],
            [InlineKeyboardButton("📋 View Full Benefits", callback_data="view_benefits")],
            [InlineKeyboardButton("❓ FAQ", callback_data="premium_faq")]
        ])
        
        await update.message.reply_text(
            "💎 *Upgrade to Premium* 💎\n\n"
            "*Premium Features:*\n"
            "✨ Unlimited daily scans\n"
            "🔮 Deep personality analysis\n"
            "🍀 Lucky numbers & meanings\n"
            "🌟 Future predictions\n"
            "📊 Detailed life statistics\n"
            "👑 Premium badge\n\n"
            "*Free Plan:* 3 scans/day\n\n"
            "Choose an option below:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard
        )

async def account_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show user account information."""
    user = update.effective_user
    db = Database()
    user_data = db.get_user(user.id)
    
    if not user_data:
        await update.message.reply_text("Please use /start first!")
        return
    
    is_premium = user_data["plan_type"] == PlanType.PREMIUM.value
    remaining = db.get_remaining_free_scans(user.id)
    
    account_text = f"""
👤 *Account Information*

*User:* {user.first_name}
*User ID:* `{user.id}`
*Plan:* {'👑 Premium' if is_premium else '🆓 Free'}

*Statistics:*
📊 Total Scans: {user_data['total_scans']}
📅 Member Since: {user_data['joined_date'][:10]}

*Today's Usage:*
{'✨ Unlimited scans' if is_premium else f'🎯 {remaining} scans remaining'}

"""
    
    if not is_premium:
        account_text += "\n💡 *Tip:* Upgrade to Premium for unlimited scans and exclusive features!"
    
    await update.message.reply_text(
        account_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_keyboard(is_premium)
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard callbacks."""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    db = Database()
    
    if query.data == "upgrade_premium":
        await query.edit_message_text(
            "💎 *Premium Subscription*\n\n"
            "This is a demo bot - premium features are simulated.\n\n"
            "In a production environment, this would connect to a payment processor like Stripe.\n\n"
            "For demo purposes, use `/upgrade_demo` to simulate premium access.",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == "view_benefits":
        benefits_text = """
🌟 *Premium Benefits Detailed*

1️⃣ *Unlimited Scans*
   No daily limits - analyze as many ages as you want!

2️⃣ *Deep Insights*
   Advanced personality profiling with AI-powered analysis

3️⃣ *Lucky Numbers*
   Personalized lucky numbers with detailed meanings

4️⃣ *Future Predictions*
   Get glimpses into potential future opportunities

5️⃣ *Priority Support*
   Direct access to our support team

6️⃣ *Early Access*
   Get new features before free users

💫 *Ready to upgrade?* Click the upgrade button below!
"""
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 Upgrade Now", callback_data="upgrade_premium")],
            [InlineKeyboardButton("◀️ Back", callback_data="back_to_premium")]
        ])
        await query.edit_message_text(
            benefits_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard
        )
    
    elif query.data == "premium_faq":
        faq_text = """
❓ *Premium FAQ*

*Q: Can I cancel anytime?*
A: Yes! No long-term contracts.

*Q: Is my payment secure?*
A: We use industry-standard encryption.

*Q: Do you offer refunds?*
A: 7-day money-back guarantee.

*Q: Can I switch plans?*
A: Upgrade/downgrade anytime.

*Q: What payment methods?*
A: All major credit cards accepted.

💫 *More questions?* Contact support!
"""
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 Upgrade to Premium", callback_data="upgrade_premium")],
            [InlineKeyboardButton("◀️ Back", callback_data="back_to_premium")]
        ])
        await query.edit_message_text(
            faq_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard
        )
    
    elif query.data == "back_to_premium":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 Upgrade to Premium ($4.99/month)", callback_data="upgrade_premium")],
            [InlineKeyboardButton("📋 View Full Benefits", callback_data="view_benefits")],
            [InlineKeyboardButton("❓ FAQ", callback_data="premium_faq")]
        ])
        await query.edit_message_text(
            "💎 *Upgrade to Premium* 💎\n\n"
            "*Premium Features:*\n"
            "✨ Unlimited daily scans\n"
            "🔮 Deep personality analysis\n"
            "🍀 Lucky numbers & meanings\n"
            "🌟 Future predictions\n"
            "📊 Detailed life statistics\n"
            "👑 Premium badge\n\n"
            "*Free Plan:* 3 scans/day\n\n"
            "Choose an option below:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard
        )

# ==================== Admin Commands ====================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin panel command."""
    user = update.effective_user
    
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized access.")
        return
    
    await update.message.reply_text(
        "🔐 *Admin Panel*\n\n"
        "Commands:\n"
        "/stats - View bot statistics\n"
        "/upgrade_user [user_id] - Upgrade user to premium\n"
        "/broadcast [message] - Send message to all users",
        parse_mode=ParseMode.MARKDOWN
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot statistics to admin."""
    user = update.effective_user
    
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized access.")
        return
    
    db = Database()
    stats = db.get_stats()
    
    stats_text = f"""
📊 *Bot Statistics*

👥 *Users:*
• Total Users: {stats['total_users']}
• Premium Users: {stats['premium_users']}
• Active Today: {stats['active_today']}

📈 *Activity:*
• Total Scans: {stats['total_scans']}
• Premium Rate: {(stats['premium_users']/stats['total_users']*100 if stats['total_users'] > 0 else 0):.1f}%

💎 *Revenue Estimate:*
• Monthly: ${stats['premium_users'] * 4.99:.2f}
"""
    
    await update.message.reply_text(
        stats_text,
        parse_mode=ParseMode.MARKDOWN
    )

async def upgrade_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to upgrade a user to premium."""
    user = update.effective_user
    
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized access.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "❌ *Usage:* `/upgrade_user [user_id]`\n"
            "Example: `/upgrade_user 123456789`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        db = Database()
        
        if db.upgrade_to_premium(target_user_id):
            await update.message.reply_text(
                f"✅ User `{target_user_id}` has been upgraded to Premium!",
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Notify the user
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text="🎉 *Congratulations!* 🎉\n\n"
                         "Your account has been upgraded to *PREMIUM*!\n\n"
                         "Enjoy unlimited scans and exclusive features! 👑",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"Failed to notify user {target_user_id}: {e}")
        else:
            await update.message.reply_text(
                f"❌ User `{target_user_id}` not found in database.",
                parse_mode=ParseMode.MARKDOWN
            )
    
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid user ID. Please provide a numeric ID.",
            parse_mode=ParseMode.MARKDOWN
        )

async def upgrade_demo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Demo command to simulate premium upgrade."""
    user = update.effective_user
    db = Database()
    
    # Create user if not exists
    db.create_or_update_user(user.id, user.username or "", user.first_name)
    
    if db.upgrade_to_premium(user.id):
        await update.message.reply_text(
            "🎉 *Welcome to Premium!* 🎉\n\n"
            "Your account has been upgraded!\n"
            "Enjoy all premium features:\n"
            "✨ Unlimited scans\n"
            "🔮 Deep insights\n"
            "🍀 Lucky numbers\n"
            "🌟 Future predictions\n\n"
            "Thank you for trying the premium demo! 👑",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_keyboard(True)
        )
    else:
        await update.message.reply_text("❌ Upgrade failed. Please try again.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel current operation."""
    context.user_data.pop("awaiting_age", None)
    
    await update.message.reply_text(
        "❌ Operation cancelled.",
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by updates."""
    logger.error(f"Update {update} caused error {context.error}")
    
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again later.\n"
                "If the problem persists, contact support."
            )
    except:
        pass

# ==================== Main Application ====================
def main() -> None:
    """Start the bot."""
   
    
    # Create custom request with proxy and timeouts
    request = HTTPXRequest(
        proxy="http://actinomycetes.demixing.pandy.rudder.vesuvius.umorina.info:36030",
        connect_timeout=30,
        read_timeout=30
    )
    
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).request(request).build()
    
    # Create conversation handler for age input
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^(🎂 Check Age|Check Age 🎂)$"), button_handler)
        ],
        states={
            WAITING_FOR_AGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_age_input)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("upgrade_user", upgrade_user_command))
    application.add_handler(CommandHandler("upgrade_demo", upgrade_demo_command))
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(
        filters.Regex(r"^(👤 My Account|My Account 👤|💎 Premium|Premium 🔥|👑 Premium)$"),
        button_handler
    ))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_error_handler(error_handler)
    
    # Start the bot
    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()