import discord
from discord.ext import commands, tasks
import sqlite3
import os
import datetime
import random
import io
import json
import base64
from PIL import Image, ImageFilter
import numpy as np
from rembg import remove, new_session
import asyncio

try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ .env file loaded")
except ImportError:
    print("ℹ️ python-dotenv not installed → using env vars only")

from onnxruntime import InferenceSession, get_available_providers

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.guild_messages = True
intents.dm_messages = True

bot = commands.Bot(command_prefix="$", intents=intents, help_command=None)
DB_PATH = "bot.db"

# Hidden timer file
TIMER_FILE = ".sys/cache/.meta-inf.dat"

# Two passwords
TRIAL_PASSWORD = "8651ProMAX@11212"
PERMANENT_PASSWORD = "21211ProMAX@8651"

def read_hidden_timestamp():
    if not os.path.exists(TIMER_FILE):
        return None
    try:
        with open(TIMER_FILE, 'r') as f:
            enc = f.read().strip()
        decoded = base64.b64decode(enc)
        key = b"x7k9pQ2m"
        ts_bytes = bytes(a ^ b for a, b in zip(decoded, key * (len(decoded) // len(key) + 1)))
        return datetime.datetime.fromisoformat(ts_bytes.decode())
    except:
        return None

def write_hidden_timestamp(dt):
    ts = dt.isoformat().encode()
    key = b"x7k9pQ2m"
    xored = bytes(a ^ b for a, b in zip(ts, key * (len(ts) // len(key) + 1)))
    enc = base64.b64encode(xored).decode()
    os.makedirs(os.path.dirname(TIMER_FILE), exist_ok=True)
    with open(TIMER_FILE, 'w') as f:
        f.write(enc)

TEMP_EXPIRES_AT = read_hidden_timestamp()

def is_bot_active():
    if not os.path.exists(TIMER_FILE):
        return True
    if TEMP_EXPIRES_AT is None:
        return False
    return datetime.datetime.now(datetime.timezone.utc) < TEMP_EXPIRES_AT

@bot.command(hidden=True)
async def activate(ctx, *, password: str):
    password = password.strip()

    if password == PERMANENT_PASSWORD:
        if os.path.exists(TIMER_FILE):
            os.remove(TIMER_FILE)
        await ctx.send("✅ Permanent unlock successful! Bot is now unlocked forever.")
        return

    if password == TRIAL_PASSWORD:
        if not os.path.exists(TIMER_FILE):
            await ctx.send("✅ Already permanently unlocked.")
            return
        if TEMP_EXPIRES_AT is not None:
            await ctx.send("⚠️ Already temporarily activated.")
            return

        expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)
        write_hidden_timestamp(expires)
        await ctx.send(f"✅ Trial activated for 2 days. Expires {expires.strftime('%Y-%m-%d %H:%M UTC')}")
        return

    await ctx.send("❌ Incorrect password.")

async def check_active(ctx):
    if not is_bot_active():
        await ctx.send("⛔ Bot is expired or not activated.\nUse `$activate <password>` to activate.")
        return False
    return True

# Force BiRefNet on CPU + fallback
try:
    rembg_session = new_session(model="birefnet-general-use", providers=["CPUExecutionProvider"])
    print("✅ Forced BiRefNet on CPU")
    REMBG_AVAILABLE = True
except Exception as e:
    print(f"⚠️ BiRefNet CPU failed: {e}")
    rembg_session = new_session("u2net_human_seg")
    print("✅ Fallback to u2net_human_seg")
    REMBG_AVAILABLE = True

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    cur.execute("""CREATE TABLE IF NOT EXISTS guild_settings (
        guild_id INTEGER PRIMARY KEY,
        admin_role INTEGER,
        helper_role INTEGER,
        gfxseller_role INTEGER,
        request_channel INTEGER,
        ticket_channel INTEGER,
        ticket_title TEXT DEFAULT '🎟️ Create a Ticket',
        ticket_desc TEXT DEFAULT 'Select a category below to open a ticket.',
        ticket_rules TEXT DEFAULT 'Please read the rules before opening a ticket.',
        calc_channel INTEGER,
        welcome_channel INTEGER,
        welcome_message TEXT DEFAULT 'Welcome {user} to the server!',
        transcript_channel INTEGER,
        bg_channel INTEGER,
        panel_message_id INTEGER,
        support_counter INTEGER DEFAULT 0,
        thumbnail_counter INTEGER DEFAULT 0,
        ticket_buttons TEXT DEFAULT '[]'
    )""")
    
    cur.execute("""CREATE TABLE IF NOT EXISTS user_data (
        guild_id INTEGER,
        user_id INTEGER,
        xp INTEGER DEFAULT 0,
        PRIMARY KEY (guild_id, user_id)
    )""")
    
    cur.execute("""CREATE TABLE IF NOT EXISTS user_pings (
        guild_id INTEGER,
        user_id INTEGER,
        ping_count INTEGER DEFAULT 0,
        last_date TEXT,
        PRIMARY KEY (guild_id, user_id)
    )""")
    
    cur.execute("""CREATE TABLE IF NOT EXISTS guild_daily (
        guild_id INTEGER PRIMARY KEY,
        pings_today INTEGER DEFAULT 0,
        messages_today INTEGER DEFAULT 0,
        last_date TEXT
    )""")
    
    cur.execute("""CREATE TABLE IF NOT EXISTS tickets (
        ticket_id TEXT PRIMARY KEY,
        channel_id INTEGER,
        guild_id INTEGER,
        user_id INTEGER,
        type TEXT,
        price INTEGER DEFAULT 0,
        open_time TEXT,
        close_time TEXT,
        claimed_by INTEGER
    )""")
    
    cur.execute("""CREATE TABLE IF NOT EXISTS giveaways (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER,
        channel_id INTEGER,
        message_id INTEGER,
        host_id INTEGER,
        prize TEXT,
        winners INTEGER,
        end_time TEXT,
        ended BOOLEAN DEFAULT 0,
        entries TEXT DEFAULT '[]'
    )""")
    
    cur.execute("""CREATE TABLE IF NOT EXISTS giveaway_claims (
        giveaway_id INTEGER,
        user_id INTEGER,
        claim_time TEXT,
        PRIMARY KEY (giveaway_id, user_id)
    )""")
    
    conn.commit()
    conn.close()

def get_settings(guild_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        cols = [desc[0] for desc in cur.description]
        settings = dict(zip(cols, row))
        settings['ticket_buttons'] = json.loads(settings.get('ticket_buttons', '[]'))
        return settings
    return None

def update_setting(guild_id, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for key, value in kwargs.items():
        if key == 'ticket_buttons':
            value = json.dumps(value)
        cur.execute(f"INSERT INTO guild_settings (guild_id, {key}) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET {key} = ?", (guild_id, value, value))
    conn.commit()
    conn.close()

def get_user_xp(guild_id, user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT xp FROM user_data WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0

def add_user_xp(guild_id, user_id, amount):
    xp = get_user_xp(guild_id, user_id) + amount
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO user_data (guild_id, user_id, xp) VALUES (?, ?, ?)", (guild_id, user_id, xp))
    conn.commit()
    conn.close()
    return xp

def update_daily_messages(guild_id):
    today = datetime.date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT last_date FROM guild_daily WHERE guild_id=?", (guild_id,))
    row = cur.fetchone()
    if row and row[0] == today:
        cur.execute("UPDATE guild_daily SET messages_today = messages_today + 1 WHERE guild_id=?", (guild_id,))
    else:
        cur.execute("INSERT OR REPLACE INTO guild_daily (guild_id, messages_today, pings_today, last_date) VALUES (?, 1, 0, ?)", (guild_id, today))
    conn.commit()
    conn.close()

def update_daily_pings(guild_id):
    today = datetime.date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT last_date FROM guild_daily WHERE guild_id=?", (guild_id,))
    row = cur.fetchone()
    if row and row[0] == today:
        cur.execute("UPDATE guild_daily SET pings_today = pings_today + 1 WHERE guild_id=?", (guild_id,))
    else:
        cur.execute("INSERT OR REPLACE INTO guild_daily (guild_id, messages_today, pings_today, last_date) VALUES (?, 0, 1, ?)", (guild_id, today))
    conn.commit()
    conn.close()

def get_daily_stats(guild_id):
    today = datetime.date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT pings_today, messages_today FROM guild_daily WHERE guild_id=? AND last_date=?", (guild_id, today))
    row = cur.fetchone()
    conn.close()
    if row:
        return row[0] or 0, row[1] or 0
    return 0, 0

def get_level_info(xp):
    if xp < 0: xp = 0
    level = 1
    total = 0
    needed = 100
    while total + needed <= xp:
        total += needed
        needed *= 2
        level += 1
    return level, xp - total, needed

def progress_bar(current, total, length=10):
    percent = min(int((current / total) * length), length) if total > 0 else length
    return "█" * percent + "░" * (length - percent)

async def is_admin(ctx):
    if ctx.author.id == ctx.guild.owner_id:
        return True
    settings = get_settings(ctx.guild.id)
    if not settings or not settings.get("admin_role"):
        await ctx.send("❌ Admin role not set.")
        return False
    admin_role = ctx.guild.get_role(settings["admin_role"])
    if not admin_role: return False
    return any(r.position >= admin_role.position for r in ctx.author.roles) or ctx.author.guild_permissions.administrator

def is_calculation(content):
    content = content.lower().strip()
    math_keywords = ['+', '-', '*', '/', '^', 'sqrt', 'sin', 'cos', 'tan', 'log', 'discount', '%']
    return any(op in content for op in math_keywords) or content.isdigit() or 'calculate' in content

def safe_calculate(expression):
    try:
        expression = expression.replace('^', '**')
        allowed_names = {"__builtins__": {}}
        import math
        result = eval(expression, allowed_names, {"math": math})
        return result
    except:
        return None

class DynamicTicketView(discord.ui.View):
    def __init__(self, bot, buttons):
        super().__init__(timeout=None)
        self.bot = bot
        for btn in buttons:
            label = btn['label']
            ticket_type = btn['type']
            style = discord.ButtonStyle.blurple if ticket_type == "support" else discord.ButtonStyle.green if ticket_type == "thumbnail" else discord.ButtonStyle.grey
            button = discord.ui.Button(label=label, style=style, custom_id=f"open_ticket_{ticket_type}")
            button.callback = self.create_ticket_callback(ticket_type)
            self.add_item(button)

    def create_ticket_callback(self, ticket_type):
        async def callback(interaction: discord.Interaction):
            if ticket_type == "support":
                await create_support_ticket(interaction)
            elif ticket_type == "thumbnail":
                await create_thumbnail_ticket(interaction, 500, interaction.user)
            else:
                embed = discord.Embed(title=f"Opening {ticket_type.capitalize()} Ticket", description="Ticket being created...", color=0x5865F2)
                await interaction.response.send_message(embed=embed, ephemeral=True)
        return callback

class TicketControlView(discord.ui.View):
    def __init__(self, bot, ticket_id: str, is_thumbnail: bool):
        super().__init__(timeout=None)
        self.bot = bot
        self.ticket_id = ticket_id
        self.is_thumbnail = is_thumbnail

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.green)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = get_settings(interaction.guild.id)
        if self.is_thumbnail and not await is_gfxseller_from_interaction(interaction) and not await is_admin_from_interaction(interaction):
            await interaction.response.send_message("❌ Only GFX Sellers can claim thumbnail tickets.", ephemeral=True)
            return
        if not self.is_thumbnail and not await is_helper_from_interaction(interaction) and not await is_admin_from_interaction(interaction):
            await interaction.response.send_message("❌ Only Helpers can claim support tickets.", ephemeral=True)
            return

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("UPDATE tickets SET claimed_by = ? WHERE ticket_id = ?", (interaction.user.id, self.ticket_id))
        conn.commit()
        conn.close()

        embed = interaction.message.embeds[0]
        embed.add_field(name="✅ Claimed By", value=interaction.user.mention, inline=False)
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(f"✅ Ticket claimed by {interaction.user.mention}", ephemeral=False)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await close_ticket(interaction, self.ticket_id, self.is_thumbnail)

async def is_admin_from_interaction(interaction):
    settings = get_settings(interaction.guild.id)
    if not settings or not settings.get("admin_role"):
        return False
    admin_role = interaction.guild.get_role(settings["admin_role"])
    if not admin_role:
        return False
    return any(r.position >= admin_role.position for r in interaction.user.roles) or interaction.user.guild_permissions.administrator

async def is_helper_from_interaction(interaction):
    settings = get_settings(interaction.guild.id)
    if not settings or not settings.get("helper_role"):
        return False
    return interaction.guild.get_role(settings["helper_role"]) in interaction.user.roles

async def is_gfxseller_from_interaction(interaction):
    settings = get_settings(interaction.guild.id)
    if not settings or not settings.get("gfxseller_role"):
        return False
    return interaction.guild.get_role(settings["gfxseller_role"]) in interaction.user.roles

async def create_support_ticket(interaction: discord.Interaction):
    if not is_bot_active():
        await interaction.response.send_message("Bot is not activated or has expired. Use `$activate <password>`", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    member = interaction.user
    settings = get_settings(guild.id)
    if not settings or not settings.get("helper_role"):
        await interaction.followup.send("❌ Helper role not set.", ephemeral=True)
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT ticket_id FROM tickets WHERE guild_id=? AND user_id=? AND close_time IS NULL", (guild.id, member.id))
    if cur.fetchone():
        await interaction.followup.send("❌ You already have an open ticket!", ephemeral=True)
        conn.close()
        return
    conn.close()

    if not await is_admin_from_interaction(interaction):
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM tickets WHERE guild_id=? AND user_id=? AND close_time IS NULL", (guild.id, member.id))
        count = cur.fetchone()[0]
        conn.close()
        if count >= 2:
            await interaction.followup.send("❌ You already have 2 open tickets. Limit is 2 for non-admins.", ephemeral=True)
            return

    counter = settings.get("support_counter", 0) + 1
    update_setting(guild.id, support_counter=counter)
    ticket_id = f"support-{counter}"

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    helper_role = guild.get_role(settings["helper_role"])
    if helper_role:
        overwrites[helper_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    admin_role = guild.get_role(settings.get("admin_role"))
    if admin_role:
        overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    channel = await guild.create_text_channel(f"ticket-{member.name}", overwrites=overwrites)

    open_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tickets (ticket_id, channel_id, guild_id, user_id, type, open_time)
        VALUES (?, ?, ?, ?, 'support', ?)
    """, (ticket_id, channel.id, guild.id, member.id, open_time))
    conn.commit()
    conn.close()

    embed = discord.Embed(title="🎟️ Support Ticket", description=f"Ticket opened by {member.mention}\n\nPlease describe your issue.", color=0x00ff00)
    view = TicketControlView(bot, ticket_id, False)
    await channel.send(embed=embed, view=view)
    await channel.send(f"{member.mention} {helper_role.mention if helper_role else ''} **Ticket opened!**")
    await interaction.followup.send(f"✅ Ticket created: {channel.mention}", ephemeral=True)

async def create_thumbnail_ticket(interaction_or_ctx, price: int, member):
    if not is_bot_active():
        if isinstance(interaction_or_ctx, discord.Interaction):
            await interaction_or_ctx.followup.send("Bot is not activated or has expired. Use `$activate <password>`", ephemeral=True)
        else:
            await interaction_or_ctx.send("Bot is not activated or has expired.")
        return
    guild = member.guild
    settings = get_settings(guild.id)
    if not settings or not settings.get("gfxseller_role"):
        if isinstance(interaction_or_ctx, discord.Interaction):
            await interaction_or_ctx.followup.send("❌ GFX Seller role not set.", ephemeral=True)
        else:
            await interaction_or_ctx.send("❌ GFX Seller role not set.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT ticket_id FROM tickets WHERE guild_id=? AND user_id=? AND close_time IS NULL", (guild.id, member.id))
    if cur.fetchone():
        if isinstance(interaction_or_ctx, discord.Interaction):
            await interaction_or_ctx.followup.send("❌ You already have an open ticket!", ephemeral=True)
        else:
            await interaction_or_ctx.send("❌ You already have an open ticket!")
        conn.close()
        return
    conn.close()

    counter = settings.get("thumbnail_counter", 0) + 1
    update_setting(guild.id, thumbnail_counter=counter)
    ticket_id = f"thumbnail-{counter}"

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    gfx_role = guild.get_role(settings["gfxseller_role"])
    if gfx_role:
        overwrites[gfx_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    helper_role = guild.get_role(settings.get("helper_role"))
    if helper_role:
        overwrites[helper_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    admin_role = guild.get_role(settings.get("admin_role"))
    if admin_role:
        overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    channel = await guild.create_text_channel(f"thumb-{counter}", overwrites=overwrites)

    open_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tickets (ticket_id, channel_id, guild_id, user_id, type, price, open_time)
        VALUES (?, ?, ?, ?, 'thumbnail', ?, ?)
    """, (ticket_id, channel.id, guild.id, member.id, price, open_time))
    conn.commit()
    conn.close()

    embed = discord.Embed(title="🖼️ Thumbnail Order", description=f"**Order ID:** {ticket_id}\n**Price:** ₹{price}\n\nRequested by {member.mention}", color=0xff00ff)
    view = TicketControlView(bot, ticket_id, True)
    await channel.send(embed=embed, view=view)
    ping_role = gfx_role or helper_role
    await channel.send(f"{member.mention} {ping_role.mention if ping_role else ''} **Thumbnail order created!**")

    if isinstance(interaction_or_ctx, discord.Interaction):
        await interaction_or_ctx.followup.send(f"✅ Thumbnail ticket created: {channel.mention}", ephemeral=True)
    else:
        await interaction_or_ctx.send(f"✅ Thumbnail ticket created: {channel.mention}")

async def close_ticket(interaction: discord.Interaction, ticket_id: str, is_thumbnail: bool):
    await interaction.response.defer()
    channel = interaction.channel
    guild = interaction.guild

    messages = []
    async for msg in channel.history(limit=None, oldest_first=True):
        time_str = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        line = f"[{time_str}] {msg.author} ({msg.author.id}): {msg.content}"
        if msg.attachments:
            line += " | Attachments: " + ", ".join(a.url for a in msg.attachments)
        messages.append(line)

    transcript_text = "\n".join(messages)
    os.makedirs(f"transcripts/{guild.id}", exist_ok=True)
    file_path = f"transcripts/{guild.id}/{ticket_id}.txt"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(transcript_text)

    settings = get_settings(guild.id)
    if settings and settings.get("transcript_channel"):
        tchnl = guild.get_channel(settings["transcript_channel"])
        if tchnl:
            file = discord.File(file_path, filename=f"{ticket_id}.txt")
            await tchnl.send(f"📄 Transcript for **{ticket_id}** (closed by {interaction.user})", file=file)

    close_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE tickets SET close_time = ? WHERE ticket_id = ?", (close_time, ticket_id))
    conn.commit()
    conn.close()

    await channel.edit(name=f"closed-{ticket_id}")

    embed = discord.Embed(title="🔒 Ticket Closed", description="This ticket has been closed. It will be automatically deleted in 10 days.", color=0xff0000)
    await channel.send(embed=embed)
    await interaction.followup.send("✅ Ticket closed. Transcript saved.", ephemeral=True)

async def animate_processing(processing_msg):
    emojis = ["⏳", "⌛"]
    i = 0
    while True:
        try:
            await processing_msg.edit(content=f"{emojis[i]} Processing...")
            i = 1 - i
            await asyncio.sleep(1)
        except:
            break

async def process_bg_removal(message: discord.Message, attachment: discord.Attachment, mode="background"):
    processing_msg = await message.reply("⏳ Processing image...")
    animate_task = asyncio.create_task(animate_processing(processing_msg))
    try:
        data = await attachment.read()
        input_img = Image.open(io.BytesIO(data)).convert("RGBA")

        if mode == "background":
            output_bytes = remove(
                data,
                session=rembg_session,
                alpha_matting=True,
                alpha_matting_foreground_threshold=245,
                alpha_matting_background_threshold=5,
                alpha_matting_erode_structure_size=8,
                post_process_mask=True
            )
            output_img = Image.open(io.BytesIO(output_bytes)).convert("RGBA")

        else:
            mask_bytes = remove(data, session=rembg_session, only_mask=True, alpha_matting=True)
            mask = Image.open(io.BytesIO(mask_bytes)).convert("L")
            blurred = input_img.filter(ImageFilter.GaussianBlur(6))
            output_img = Image.composite(input_img.convert("RGB"), blurred.convert("RGB"), mask)

        output_file = io.BytesIO()
        output_img.save(output_file, format="PNG")
        output_file.seek(0)

        filename = "bg_removed_transparent.png" if mode == "background" else "character_blurred.png"
        file = discord.File(output_file, filename=filename)

        reply_text = (
            f"{message.author.mention} ✅ Done!\n"
            f"{'Background completely removed → fully transparent PNG' if mode == 'background' else 'Character blurred (original background kept)'}"
        )
        await message.reply(reply_text, file=file)

    except Exception as e:
        await message.reply(f"❌ Failed: {str(e)[:200]}")
    finally:
        animate_task.cancel()
        await processing_msg.delete()

@bot.event
async def on_ready():
    init_db()
    status = "PERMANENT (timer file missing)" if not os.path.exists(TIMER_FILE) else \
             ("TEMPORARY until " + TEMP_EXPIRES_AT.strftime('%Y-%m-%d %H:%M UTC') if TEMP_EXPIRES_AT else "LOCKED")
    print(f"✅ Bot is online as {bot.user} | Activation: {status}")
    
    bot.add_view(DynamicTicketView(bot, []))
    
    for guild in bot.guilds:
        settings = get_settings(guild.id)
        if settings and settings.get("ticket_channel") and settings.get("panel_message_id"):
            channel = guild.get_channel(settings["ticket_channel"])
            if channel:
                try:
                    msg = await channel.fetch_message(settings["panel_message_id"])
                    buttons = settings.get("ticket_buttons", [])
                    await msg.edit(view=DynamicTicketView(bot, buttons))
                except:
                    pass
    cleanup_tickets.start()
    check_giveaways.start()

@tasks.loop(minutes=30)
async def cleanup_tickets():
    now = datetime.datetime.now(datetime.timezone.utc)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT channel_id, close_time FROM tickets WHERE close_time IS NOT NULL")
    rows = cur.fetchall()
    for channel_id, close_str in rows:
        close_time = datetime.datetime.fromisoformat(close_str)
        if (now - close_time) > datetime.timedelta(days=10):
            channel = bot.get_channel(channel_id)
            if channel:
                try:
                    await channel.delete(reason="Auto-delete after 10 days")
                except:
                    pass
            cur.execute("DELETE FROM tickets WHERE channel_id=?", (channel_id,))
    conn.commit()
    conn.close()

@tasks.loop(minutes=1)
async def check_giveaways():
    now = datetime.datetime.now(datetime.timezone.utc)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, guild_id, channel_id, message_id, winners, prize FROM giveaways WHERE ended=0 AND end_time < ?", (now.isoformat(),))
    rows = cur.fetchall()
    for row in rows:
        giveaway_id, guild_id, channel_id, message_id, winners, prize = row
        guild = bot.get_guild(guild_id)
        if not guild:
            continue
        channel = guild.get_channel(channel_id)
        if not channel:
            continue
        try:
            msg = await channel.fetch_message(message_id)
            cur.execute("SELECT entries FROM giveaways WHERE id=?", (giveaway_id,))
            entries = json.loads(cur.fetchone()[0])
            if len(entries) < winners:
                await msg.edit(content=f"Giveaway ended! Not enough entries for {winners} winners. Prize: **{prize}**")
            else:
                winner_ids = random.sample(entries, winners)
                winners_str = ", ".join(f"<@{uid}>" for uid in winner_ids)
                await msg.edit(content=f"Giveaway ended! Winners: {winners_str}\nPrize: **{prize}**\n\nWinners: DM the bot with `claim {giveaway_id}` to claim your prize!")
            cur.execute("UPDATE giveaways SET ended=1 WHERE id=?", (giveaway_id,))
            conn.commit()
        except:
            pass
    conn.close()

@bot.event
async def on_member_join(member: discord.Member):
    if not is_bot_active():
        return
    settings = get_settings(member.guild.id)
    if not settings or not settings.get("welcome_channel"):
        return
    channel = member.guild.get_channel(settings["welcome_channel"])
    if not channel:
        return
    msg = settings["welcome_message"].replace("{user}", member.mention)
    embed = discord.Embed(description=msg, color=0x00ff00)
    embed.set_thumbnail(url=member.display_avatar.url)
    await channel.send(embed=embed)

@bot.event
async def on_raw_reaction_add(payload):
    if payload.emoji.name != "🎉":
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, entries, ended FROM giveaways WHERE message_id=? AND guild_id=?", (payload.message_id, payload.guild_id))
    row = cur.fetchone()
    if row and row[2] == 0:
        giveaway_id, entries_json, _ = row
        entries = json.loads(entries_json)
        if payload.user_id not in entries and not bot.get_user(payload.user_id).bot:
            entries.append(payload.user_id)
            cur.execute("UPDATE giveaways SET entries=? WHERE id=?", (json.dumps(entries), giveaway_id))
            conn.commit()
    conn.close()

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        await bot.process_commands(message)
        return

    if not is_bot_active():
        if message.content.lower().startswith("$activate"):
            await bot.process_commands(message)
        return

    if message.guild:
        guild_id = message.guild.id
        settings = get_settings(guild_id)

        update_daily_messages(guild_id)

        if random.random() < 0.7:
            add_user_xp(guild_id, message.author.id, random.randint(8, 20))

        # Ping limit (5/day, admins exempt)
        if message.mentions or "@everyone" in message.content or "@here" in message.content:
            if await is_admin(message.author):
                pass  # admins unlimited
            else:
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("SELECT ping_count FROM user_pings WHERE guild_id=? AND user_id=?", (guild_id, message.author.id))
                row = cur.fetchone()
                ping_count = row[0] if row and row[1] == datetime.date.today().isoformat() else 0

                if ping_count >= 5:
                    await message.delete()
                    await message.channel.send(f"{message.author.mention} You have reached the daily ping limit (5/day). No more mentions today.")
                else:
                    new_count = ping_count + 1
                    cur.execute("INSERT OR REPLACE INTO user_pings (guild_id, user_id, ping_count, last_date) VALUES (?, ?, ?, ?)",
                                (guild_id, message.author.id, new_count, datetime.date.today().isoformat()))
                    conn.commit()
                conn.close()

        if settings and message.channel.id == settings.get("calc_channel"):
            content = message.content.strip().lower()

            if content.startswith("discount ") or "discount" in content:
                try:
                    parts = content.split()
                    if len(parts) >= 3:
                        price = float(parts[1])
                        disc = float(parts[2].replace('%', ''))
                        discount_amount = price * (disc / 100)
                        final = price - discount_amount
                        await message.reply(
                            f"Original: ₹{price:,.2f}\n"
                            f"Discount: {disc}% (₹{discount_amount:,.2f})\n"
                            f"Final price: **₹{final:,.2f}**"
                        )
                        return
                except:
                    await message.reply("Invalid discount format. Try: `1200 discount 15`")
                    return

            if is_calculation(content):
                result = safe_calculate(content)
                if result is not None:
                    await message.reply(f"**{content} =** `{result:,.2f}`")
                else:
                    await message.reply("Invalid calculation.")
                return

        if settings and message.channel.id == settings.get("bg_channel") and message.attachments:
            for att in message.attachments:
                if att.content_type and att.content_type.startswith("image"):
                    mode_msg = await message.reply(
                        f"{message.author.mention} Image received!\n\n"
                        "Reply with one word:\n"
                        "**Background** → Remove background (transparent, keep person/object)\n"
                        "**Character** → Blur person (keep original bg)"
                    )

                    def check(m):
                        return m.author == message.author and m.channel == message.channel and m.reference and m.reference.message_id == mode_msg.id

                    try:
                        reply = await bot.wait_for("message", check=check, timeout=60.0)
                        choice = reply.content.strip().lower()

                        if "background" in choice:
                            await process_bg_removal(message, att, mode="background")
                        elif "character" in choice or "person" in choice:
                            await process_bg_removal(message, att, mode="character")
                        else:
                            await reply.reply("Invalid choice. Use **Background** or **Character** only.")

                        await asyncio.sleep(10)
                        await mode_msg.delete()

                    except asyncio.TimeoutError:
                        await mode_msg.edit(content="Timed out.")
                        await mode_msg.delete(delay=5)

                    break

    else:
        content = message.content.strip().lower()
        if content.startswith("claim "):
            try:
                giveaway_id = int(content.split()[1])
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("SELECT end_time, entries, host_id, guild_id FROM giveaways WHERE id=? AND ended=1", (giveaway_id,))
                row = cur.fetchone()
                if row:
                    end_time_str, entries_json, host_id, guild_id = row
                    entries = json.loads(entries_json)
                    if message.author.id in entries:
                        claim_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        cur.execute("INSERT OR REPLACE INTO giveaway_claims (giveaway_id, user_id, claim_time) VALUES (?, ?, ?)", (giveaway_id, message.author.id, claim_time))
                        conn.commit()
                        await message.reply(f"✅ Claim recorded for giveaway {giveaway_id}. The host has been notified.")
                        host = await bot.fetch_user(host_id)
                        if host:
                            await host.send(f"User {message.author} ({message.author.id}) claimed giveaway {giveaway_id} at {claim_time}.")
                    else:
                        await message.reply("❌ You are not a winner of this giveaway.")
                else:
                    await message.reply("❌ Giveaway not found or not ended.")
                conn.close()
            except:
                await message.reply("Invalid claim format. Use 'claim <giveaway_id>'.")

    await bot.process_commands(message)

@bot.command()
async def assignadminrole(ctx, role: discord.Role):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    update_setting(ctx.guild.id, admin_role=role.id)
    await ctx.send(f"✅ Admin role set to **{role.name}**")

@bot.command()
async def assignhelper(ctx, role: discord.Role):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    update_setting(ctx.guild.id, helper_role=role.id)
    await ctx.send(f"✅ Helper role set to **{role.name}**")

@bot.command()
async def assigngfxseller(ctx, role: discord.Role):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    update_setting(ctx.guild.id, gfxseller_role=role.id)
    await ctx.send(f"✅ GFX Seller role set to **{role.name}**")

@bot.command()
async def setrequestchnl(ctx, channel: discord.TextChannel):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    update_setting(ctx.guild.id, request_channel=channel.id)
    await ctx.send(f"✅ Request channel set to {channel.mention}")

@bot.command()
async def setticketchnl(ctx, channel: discord.TextChannel):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    settings = get_settings(ctx.guild.id)
    if settings and settings.get("panel_message_id"):
        try:
            old = await channel.fetch_message(settings["panel_message_id"])
            await old.delete()
        except:
            pass
    update_setting(ctx.guild.id, ticket_channel=channel.id)
    embed = discord.Embed(
        title=settings.get("ticket_title", "🎟️ Create a Ticket"),
        description=settings.get("ticket_desc", "") + "\n\n" + settings.get("ticket_rules", ""),
        color=0x7289da
    )
    view = DynamicTicketView(bot, settings.get("ticket_buttons", []))
    msg = await channel.send(embed=embed, view=view)
    update_setting(ctx.guild.id, panel_message_id=msg.id)
    await ctx.send(f"✅ Ticket panel sent in {channel.mention}")

@bot.command()
async def settickettitle(ctx, *, title: str):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    update_setting(ctx.guild.id, ticket_title=title)
    await ctx.send(f"✅ Ticket title set to: **{title}**")

@bot.command()
async def setticketdesc(ctx, *, desc: str):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    update_setting(ctx.guild.id, ticket_desc=desc)
    await ctx.send(f"✅ Ticket description set.")

@bot.command()
async def setticketrules(ctx, *, rules: str):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    update_setting(ctx.guild.id, ticket_rules=rules)
    await ctx.send(f"✅ Ticket rules set.")

@bot.command()
async def setcalculatorchnl(ctx, channel: discord.TextChannel):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    update_setting(ctx.guild.id, calc_channel=channel.id)
    await ctx.send(f"✅ Calculator channel set to {channel.mention}")

@bot.command()
async def setbgchnl(ctx, channel: discord.TextChannel):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    update_setting(ctx.guild.id, bg_channel=channel.id)
    await ctx.send(f"✅ Background removal channel set to {channel.mention}")

@bot.command()
async def transcriptchnl(ctx, channel: discord.TextChannel):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    update_setting(ctx.guild.id, transcript_channel=channel.id)
    await ctx.send(f"✅ Transcript channel set to {channel.mention}")

@bot.command()
async def setwlcmmessage(ctx, *, text: str):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    update_setting(ctx.guild.id, welcome_message=text)
    await ctx.send(f"✅ Welcome message set.")

@bot.command()
async def setwlcmchnl(ctx, channel: discord.TextChannel):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    update_setting(ctx.guild.id, welcome_channel=channel.id)
    await ctx.send(f"✅ Welcome channel set to {channel.mention}")

@bot.command()
async def removewlcmchnl(ctx):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    update_setting(ctx.guild.id, welcome_channel=None)
    await ctx.send("✅ Welcome channel removed.")

@bot.command()
async def say(ctx, channel: discord.TextChannel, *, text: str):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    await channel.send(text)

@bot.command()
async def serverinfo(ctx):
    if not await check_active(ctx): return
    members = len(ctx.guild.members)
    bots = sum(1 for m in ctx.guild.members if m.bot)
    pings, msgs = get_daily_stats(ctx.guild.id)
    now_gmt = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S GMT")
    embed = discord.Embed(title=f"📊 {ctx.guild.name} Info", color=0x7289da)
    embed.add_field(name="Members", value=members, inline=True)
    embed.add_field(name="Bots", value=bots, inline=True)
    embed.add_field(name="Pings Today", value=pings, inline=True)
    embed.add_field(name="Messages Today", value=msgs, inline=True)
    embed.add_field(name="GMT Time", value=now_gmt, inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def userinfo(ctx, member: discord.Member = None):
    if not await check_active(ctx): return
    member = member or ctx.author
    embed = discord.Embed(title=f"{member} Info", color=0x7289da)
    embed.set_thumbnail(url=member.display_avatar.url)
    if member.banner:
        embed.set_image(url=member.banner.url)
    embed.add_field(name="Username", value=member.name, inline=True)
    embed.add_field(name="User ID", value=member.id, inline=True)
    embed.add_field(name="Joined Server", value=member.joined_at.strftime("%d %b %Y") if member.joined_at else "N/A", inline=True)
    embed.add_field(name="Account Created", value=member.created_at.strftime("%d %b %Y"), inline=True)
    await ctx.send(embed=embed)

@bot.command()
async def stats(ctx, member: discord.Member = None):
    if not await check_active(ctx): return
    member = member or ctx.author
    xp = get_user_xp(ctx.guild.id, member.id)
    level, current, needed = get_level_info(xp)
    bar = progress_bar(current, needed)
    embed = discord.Embed(title=f"📈 {member.name}'s Level Stats", color=0x00ff00)
    embed.add_field(name="Level", value=level, inline=True)
    embed.add_field(name="Total XP", value=f"{xp:,}", inline=True)
    embed.add_field(name="Progress", value=f"{current:,} / {needed:,} XP\n{bar}", inline=False)
    embed.set_footer(text=f"Next level requires {needed * 2:,} total XP")
    await ctx.send(embed=embed)

@bot.command()
async def thumbnail(ctx, price: int):
    if not await check_active(ctx): return
    await create_thumbnail_ticket(ctx, price, ctx.author)

@bot.command()
async def lock(ctx):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    settings = get_settings(ctx.guild.id)
    admin_role = ctx.guild.get_role(settings.get("admin_role")) if settings else None
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
    if admin_role:
        await ctx.channel.set_permissions(admin_role, send_messages=True)
    await ctx.send("🔒 Channel locked.")

@bot.command()
async def unlock(ctx):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=None)
    settings = get_settings(ctx.guild.id)
    if settings and settings.get("admin_role"):
        admin_role = ctx.guild.get_role(settings["admin_role"])
        if admin_role:
            await ctx.channel.set_permissions(admin_role, send_messages=None)
    await ctx.send("🔓 Channel unlocked.")

@bot.command()
async def commands(ctx):
    if not await check_active(ctx): return
    embed = discord.Embed(
        title="📜 ProMAX Bot Commands",
        description="All commands with usage. Prefix: $ • Admin commands need admin role",
        color=0x5865F2,
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else bot.user.avatar.url)
    embed.set_footer(text="Type in calculator channel for math/discounts • Contact admin for help")

    embed.add_field(
        name="Everyone Commands",
        value="`$serverinfo` → Server stats & info\n"
              "`$userinfo [@user]` → Profile, ID, join date\n"
              "`$stats [@user]` → Level & XP progress\n"
              "`$thumbnail <price>` → Open GFX thumbnail ticket\n"
              "**BG channel**: Upload image → reply **Background** or **Character**\n"
              "**Calculator channel**: Math like `500 + 200`, `1200 discount 15`\n"
              "`$commands` → This menu",
        inline=False
    )

    embed.add_field(
        name="Admin & Management",
        value="`$assignadminrole @role` → Set admin role\n"
              "`$assignhelper @role` → Set helper role\n"
              "`$assigngfxseller @role` → Set GFX seller role\n"
              "`$setrequestchnl #channel` → Ping limit channel\n"
              "`$setticketchnl #channel` → Ticket panel channel\n"
              "`$setbgchnl #channel` → Background removal channel\n"
              "`$setcalculatorchnl #channel` → Math calculator channel\n"
              "`$transcriptchnl #channel` → Transcript log channel\n"
              "`$setwlcmchnl #channel` → Welcome channel\n"
              "`$setwlcmmessage \"text\"` → Custom welcome message\n"
              "`$removewlcmchnl` → Remove welcome channel\n"
              "`$lock` / `$unlock` → Lock/unlock channel\n"
              "`$say #channel text` → Send message silently\n"
              "`$kick @user [reason]` → Kick member\n"
              "`$ban @user [reason]` → Ban member",
        inline=False
    )

    embed.add_field(
        name="Ticket Panel (Admin)",
        value="`$addticketbutton \"Label\" \"type\" \"desc\"` → Add category button\n"
              "`$removeticketbutton \"Label\"` → Remove button\n"
              "`$resetticketpanel` → Rebuild panel with current buttons\n"
              "`$settickettitle \"title\"` → Change panel title\n"
              "`$setticketdesc \"desc\"` → Change description\n"
              "`$setticketrules \"rules\"` → Set rules text",
        inline=False
    )

    await ctx.send(embed=embed)

@bot.command()
async def kick(ctx, member: discord.Member, *, reason: str = None):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    await member.kick(reason=reason)
    await ctx.send(f"✅ Kicked **{member}**")

@bot.command()
async def ban(ctx, member: discord.Member, *, reason: str = None):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    await member.ban(reason=reason)
    await ctx.send(f"✅ Banned **{member}**")

@bot.command()
async def addticketbutton(ctx, label: str, ticket_type: str, *, description: str = "No description"):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    settings = get_settings(ctx.guild.id)
    if not settings:
        await ctx.send("Guild settings not found.")
        return

    buttons = settings['ticket_buttons']
    buttons.append({"label": label, "type": ticket_type.lower(), "desc": description})

    update_setting(ctx.guild.id, ticket_buttons=buttons)

    if settings.get("ticket_channel"):
        channel = ctx.guild.get_channel(settings["ticket_channel"])
        if channel and settings.get("panel_message_id"):
            try:
                msg = await channel.fetch_message(settings["panel_message_id"])
                await msg.edit(view=DynamicTicketView(bot, buttons))
            except:
                pass

    await ctx.send(f"✅ Added button: **{label}** (type: {ticket_type})")

@bot.command()
async def removeticketbutton(ctx, *, label: str):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    settings = get_settings(ctx.guild.id)
    if not settings:
        await ctx.send("Guild settings not found.")
        return

    buttons = settings['ticket_buttons']
    new_buttons = [b for b in buttons if b['label'].lower() != label.lower()]

    if len(new_buttons) == len(buttons):
        await ctx.send(f"Button **{label}** not found.")
        return

    update_setting(ctx.guild.id, ticket_buttons=new_buttons)

    if settings.get("ticket_channel"):
        channel = ctx.guild.get_channel(settings["ticket_channel"])
        if channel and settings.get("panel_message_id"):
            try:
                msg = await channel.fetch_message(settings["panel_message_id"])
                await msg.edit(view=DynamicTicketView(bot, new_buttons))
            except:
                pass

    await ctx.send(f"✅ Removed button: **{label}**")

@bot.command()
async def resetticketpanel(ctx):
    if not await check_active(ctx): return
    if not await is_admin(ctx): return
    settings = get_settings(ctx.guild.id)
    if not settings or not settings.get("ticket_channel"):
        await ctx.send("Ticket channel not set.")
        return

    channel = ctx.guild.get_channel(settings["ticket_channel"])
    if not channel:
        await ctx.send("Channel not found.")
        return

    if settings.get("panel_message_id"):
        try:
            old_msg = await channel.fetch_message(settings["panel_message_id"])
            await old_msg.delete()
        except:
            pass

    embed = discord.Embed(
        title=settings.get("ticket_title", "🎟️ Create a Ticket"),
        description=settings.get("ticket_desc", "Select a category below."),
        color=0x5865F2,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="Read rules before opening")
    buttons = settings.get("ticket_buttons", [])
    view = DynamicTicketView(bot, buttons)
    msg = await channel.send(embed=embed, view=view)

    update_setting(ctx.guild.id, panel_message_id=msg.id)

    await ctx.send("✅ Ticket panel reset with current buttons.")

if __name__ == "__main__":
    token = os.getenv("TOKEN")
    if not token:
        print("❌ TOKEN NOT FOUND!")
        print("   → Go to LemonHost dashboard → Environment Variables")
        print("   → Add key: TOKEN   value: your_bot_token")
    else:
        print("🚀 Starting bot...")
        bot.run(token)
