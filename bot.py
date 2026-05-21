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

# --- Désactivation de l'aide par défaut de Discord ---
bot.remove_command('help')

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


# --- Administrative & Moderation Commands ---

@bot.command(name="mute")
@commands.has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, minutes: str, *, reason: str):
    """Mutes a member. Syntax: !mute @member minutes reason"""
    try:
        clean_minutes = int(''.join(filter(str.isdigit, minutes)))
        await member.timeout(timedelta(minutes=clean_minutes), reason=reason)
        await ctx.send(f"✅ {member.mention} has been muted for {clean_minutes} minutes. Reason: {reason}")
    except ValueError:
        await ctx.send("❌ Invalid duration. Please provide a valid number of minutes (e.g., 10 or 10m).")
    except discord.Forbidden:
        await ctx.send("❌ I do not have permissions to timeout this member.")

@bot.command(name="unmute")
@commands.has_permissions(moderate_members=True)
async def unmute(ctx, member: discord.Member):
    """Removes timeout from a member."""
    try:
        await member.timeout(None)
        await ctx.send(f"✅ {member.mention} is no longer muted.")
    except discord.Forbidden:
        await ctx.send("❌ Unable to remove timeout for this member.")

@bot.command(name="kick")
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """Kicks a member from the server."""
    try:
        await member.kick(reason=reason)
        await ctx.send(f"✅ {member.mention} has been kicked. Reason: {reason}")
    except discord.Forbidden:
        await ctx.send("❌ I do not have permissions to kick this member.")

@bot.command(name="ban")
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """Permanently bans a member from the server."""
    try:
        await member.ban(reason=reason)
        await ctx.send(f"✅ {member.mention} has been permanently banned. Reason: {reason}")
    except discord.Forbidden:
        await ctx.send("❌ I do not have permissions to ban this member.")

@bot.command(name="lock")
@commands.has_permissions(manage_channels=True)
async def lock(ctx):
    """Locks the current text channel."""
    try:
        await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
        await ctx.send("🔒 This channel has been locked.")
    except discord.Forbidden:
        await ctx.send("❌ I do not have permissions to lock this channel.")

@bot.command(name="unlock")
@commands.has_permissions(manage_channels=True)
async def unlock(ctx):
    """Unlocks the current text channel."""
    try:
        await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=None)
        await ctx.send("🔓 This channel is now unlocked.")
    except discord.Forbidden:
        await ctx.send("❌ I do not have permissions to unlock this channel.")

@bot.command(name="purge")
@commands.has_permissions(manage_messages=True)
async def purge(ctx, amount: int):
    """Deletes a specified number of messages."""
    try:
        deleted = await ctx.channel.purge(limit=amount + 1)
        await ctx.send(f"🗑️ Deleted {len(deleted) - 1} messages.", delete_after=5)
    except discord.Forbidden:
        await ctx.send("❌ I do not have permissions to purge messages in this channel.")

@bot.command(name="slowmode")
@commands.has_permissions(manage_channels=True)
async def slowmode(ctx, seconds: int):
    """Changes the slowmode delay of the current channel."""
    try:
        await ctx.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            await ctx.send("⏱️ Slowmode has been disabled.")
        else:
            await ctx.send(f"⏱️ Slowmode set to {seconds} seconds.")
    except discord.Forbidden:
        await ctx.send("❌ I do not have permissions to change slowmode.")


# --- Informational Commands ---

@bot.command(name="userinfo")
async def userinfo(ctx, member: discord.Member = None):
    """Displays detailed information about a member."""
    member = member or ctx.author
    roles = [role.mention for role in member.roles if role != ctx.guild.default_role]
    
    embed = discord.Embed(title=f"👤 User Info - {member.name}", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=member.id, inline=True)
    embed.add_field(name="Nickname", value=member.display_name, inline=True)
    embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d"), inline=False)
    embed.add_field(name="Joined Server", value=member.joined_at.strftime("%Y-%m-%d"), inline=False)
    embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles) if roles else "None", inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name="serverinfo")
async def serverinfo(ctx):
    """Displays information about the server."""
    guild = ctx.guild
    text_channels = len(guild.text_channels)
    voice_channels = len(guild.voice_channels)
    
    embed = discord.Embed(title=f"🏰 Server Info - {guild.name}", color=discord.Color.blue())
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
        
    embed.add_field(name="Owner", value=guild.owner.mention if guild.owner else "Unknown", inline=True)
    embed.add_field(name="Server ID", value=guild.id, inline=True)
    embed.add_field(name="Total Members", value=guild.member_count, inline=True)
    embed.add_field(name="Channels", value=f"📝 Text: {text_channels} | 🔊 Voice: {voice_channels}", inline=False)
    embed.add_field(name="Created On", value=guild.created_at.strftime("%Y-%m-%d"), inline=False)
    
    await ctx.send(embed=embed)


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
        name="👥 General & Info Commands",
        value=(
            "`!cmds` : Displays this help menu.\n"
            "`!userinfo [@member]` : Shows detailed info about a user.\n"
            "`!serverinfo` : Displays useful server statistics."
        ),
        inline=False
    )
    
    embed.add_field(
        name="🛡️ Moderation Commands",
        value=(
            "`!mute <@member> <minutes> <reason>` : Temporarily mutes a member.\n"
            "`!unmute <@member>` : Removes the timeout from a member.\n"
            "`!kick <@member> [reason]` : Kicks a member from the server.\n"
            "`!ban <@member> [reason]` : Permanently bans a member from the server.\n"
            "`!purge <number>` : Deletes a specific number of recent messages."
        ),
        inline=False
    )

    embed.add_field(
        name="⚙️ Management Commands",
        value=(
            "`!lock` : Disables sending messages in the current channel.\n"
            "`!unlock` : Restores message permissions in the current channel.\n"
            "`!slowmode <seconds>` : Sets a message cooldown for the current channel (use 0 to disable)."
        ),
        inline=False
    )
    
    embed.set_footer(text="Automated anti-spam & anti-profanity active (3 warnings = Kick)")
    
    await ctx.send(embed=embed)

bot.run(os.getenv("DISCORD_TOKEN"))
