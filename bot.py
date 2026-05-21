import discord
from discord.ext import commands
import asyncio
import re
import sqlite3
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database setup
conn = sqlite3.connect("warnings.db")
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS warnings (
        user_id INTEGER PRIMARY KEY,
        warnings INTEGER DEFAULT 0,
        reasons TEXT
    )
""")
conn.commit()

user_messages = {}
INSULTES = [ r"\bidiot\b", r"\basshole\b" ] 

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot connected as: {bot.user}")

@bot.event
async def on_message(message):
    if message.author == bot.user or not message.guild or message.author.bot:
        return

    if message.author.guild_permissions.manage_messages:
        await bot.process_commands(message)
        return

    user_id = message.author.id
    current_time = datetime.now()

    # --- Spam Detection ---
    if user_id not in user_messages:
        user_messages[user_id] = []
    user_messages[user_id].append(current_time)
    user_messages[user_id] = [t for t in user_messages[user_id] if (current_time - t).total_seconds() <= 15]

    if len(user_messages[user_id]) > 4:
        user_messages[user_id] = []
        await handle_mute_and_warn(message, "Spam detected (4 messages in 15 sec)")
        return

    # --- Auto Profanity Detection ---
    contenu = message.content.lower()
    for insulte in INSULTES:
        if re.search(insulte, contenu):
            await handle_mute_and_warn(message, "Inappropriate language")
            return

    await bot.process_commands(message)

async def handle_mute_and_warn(message, reason):
    user_id = message.author.id
    
    cursor.execute("""
        INSERT INTO warnings (user_id, warnings, reasons)
        VALUES (?, 1, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            warnings = warnings + 1,
            reasons = coalesce(reasons, '') || '\n' || ?
    """, (user_id, reason, reason))
    conn.commit()

    cursor.execute("SELECT warnings FROM warnings WHERE user_id = ?", (user_id,))
    warnings_count = cursor.fetchone()[0]

    try:
        await message.delete()

        if warnings_count >= 3:
            await message.author.kick(reason=f"3 warnings reached: {reason}")
            await message.channel.send(f"❌ {message.author.mention} has been **kicked** after reaching 3 warnings.")
            cursor.execute("DELETE FROM warnings WHERE user_id = ?", (user_id,))
            conn.commit()
        else:
            await message.author.timeout(timedelta(minutes=10), reason=reason)
            await message.channel.send(
                f"⚠️ {message.author.mention} has been timed out (10 min). Reason: {reason} ({warnings_count}/3)"
            )
    except discord.Forbidden:
        print(f"Missing permissions to take action against {message.author.name}")

# --- Administrative Commands ---

@bot.command(name="mute")
@commands.has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, minutes: int, *, reason: str):
    """Mutes a member. Syntax: !mute @member minutes reason"""
    try:
        await member.timeout(timedelta(minutes=minutes), reason=reason)
        await ctx.send(f"✅ {member.mention} has been muted for {minutes} minutes. Reason: {reason}")
    except discord.Forbidden:
        await ctx.send("❌ I do not have permissions to timeout this member.")

@bot.command(name="unmute")
@commands.has_permissions(moderate_members=True)
async def unmute(ctx, member: discord.Member):
    try:
        await member.timeout(None)
        await ctx.send(f"✅ {member.mention} is no longer muted.")
    except discord.Forbidden:
        await ctx.send("❌ Unable to remove timeout for this member.")

# --- Help Command ---

@bot.command(name="cmds")
async def cmds(ctx):
    """Displays the list of all available commands on the server."""
    embed = discord.Embed(
        title="📜 Server Commands List",
        description="Here are the commands you can use with the `!` prefix",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="👥 General Commands",
        value="`!cmds` : Displays this help menu.",
        inline=False
    )
    
    embed.add_field(
        name="🛡️ Moderation Commands",
        value=(
            "`!mute <@member> <minutes> <reason>` : Temporarily mutes a member.\n"
            "`!unmute <@member>` : Removes the timeout from a member."
        ),
        inline=False
    )
    
    embed.set_footer(text="Automated anti-spam & anti-profanity active (3 warnings = Kick)")
    
    await ctx.send(embed=embed)

bot.run(os.getenv("DISCORD_TOKEN"))
