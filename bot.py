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

# Existing warnings table
cursor.execute("""
    CREATE TABLE IF NOT EXISTS warnings (
        user_id INTEGER PRIMARY KEY,
        warnings INTEGER DEFAULT 0,
        reasons TEXT
    )
""")

# New table for Welcome Configuration
cursor.execute("""
    CREATE TABLE IF NOT EXISTS welcome_config (
        guild_id INTEGER PRIMARY KEY,
        channel_id INTEGER
    )
""")

# New table for Ticket Configuration
cursor.execute("""
    CREATE TABLE IF NOT EXISTS ticket_config (
        guild_id INTEGER PRIMARY KEY,
        category_id INTEGER
    )
""")
conn.commit()

user_messages = {}
INSULTES = [ r"\bidiot\b", r"\basshole\b" ] 

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Persistent Ticket View ---
class TicketButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Persistent view

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.green, custom_id="create_ticket_btn", emoji="📩")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild.id
        
        # Get configured category
        cursor.execute("SELECT category_id FROM ticket_config WHERE guild_id = ?", (guild_id,))
        row = cursor.fetchone()
        
        if not row:
            await interaction.response.send_message("❌ Tickets are not configured yet. Please ask an admin to run `!ticketconfig`.", ephemeral=True)
            return
            
        category_id = row[0]
        category = interaction.guild.get_channel(category_id)
        
        if not category:
            await interaction.response.send_message("❌ Configured ticket category not found. Please reconfigure using `!ticketconfig`.", ephemeral=True)
            return

        # Create the ticket channel
        channel_name = f"ticket-{interaction.user.name}"
        
        # Set permissions: Allow user and staff, deny @everyone
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        ticket_channel = await interaction.guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            reason=f"Ticket created by {interaction.user}"
        )
        
        # Send initial message inside the ticket
        embed = discord.Embed(
            title="🎫 Ticket Created",
            description=f"Welcome {interaction.user.mention},\n\nSupport staff will be with you shortly. Please describe your issue in detail.",
            color=discord.Color.green()
        )
        
        # Simple view to close the ticket
        close_view = discord.ui.View(timeout=None)
        close_button = discord.ui.Button(label="Close Ticket", style=discord.ButtonStyle.red, custom_id="close_ticket_btn", emoji="🔒")
        
        async def close_callback(close_interaction: discord.Interaction):
            await close_interaction.response.send_message("🔒 This ticket will close in 5 seconds...")
            await asyncio.sleep(5)
            await close_interaction.channel.delete()
            
        close_button.callback = close_callback
        close_view.add_item(close_button)
        
        await ticket_channel.send(embed=embed, view=close_view)
        await interaction.response.send_message(f"✅ Ticket created! Go to {ticket_channel.mention}", ephemeral=True)


@bot.event
async def on_ready():
    # Register the persistent ticket button so it works after reboots
    bot.add_view(TicketButtonView())
    print(f"Bot connected as: {bot.user}")

# --- Welcome Event ---
@bot.event
async def on_member_join(member):
    cursor.execute("SELECT channel_id FROM welcome_config WHERE guild_id = ?", (member.guild.id,))
    row = cursor.fetchone()
    if row:
        channel_id = row[0]
        channel = member.guild.get_channel(channel_id)
        if channel:
            await channel.send(f"👋 Welcome to the server, {member.mention}! We are glad to have you here! 🎉")

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


# --- Administrative, Moderation & Configuration Commands ---

@bot.command(name="welcome")
@commands.has_permissions(manage_guild=True)
async def welcome(ctx):
    """Interactive setup for the welcome channel."""
    await ctx.send("📝 Please mention the channel where welcome messages should be sent (e.g., #welcome):")
    
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    try:
        msg = await bot.wait_for("message", check=check, timeout=30.0)
        if msg.channel_mentions:
            target_channel = msg.channel_mentions[0]
            
            cursor.execute("""
                INSERT INTO welcome_config (guild_id, channel_id) 
                VALUES (?, ?) 
                ON CONFLICT(guild_id) DO UPDATE SET channel_id = ?
            """, (ctx.guild.id, target_channel.id, target_channel.id))
            conn.commit()
            
            await ctx.send(f"✅ Welcome messages will now be sent to {target_channel.mention} in English.")
        else:
            await ctx.send("❌ Setup canceled. You didn't mention a valid text channel.")
    except asyncio.TimeoutError:
        await ctx.send("❌ Setup timed out. Please try running `!welcome` again.")

@bot.command(name="ticketconfig")
@commands.has_permissions(manage_guild=True)
async def ticketconfig(ctx):
    """Interactive setup for tickets and button setup."""
    await ctx.send("📝 Please provide the exact ID of the **Category** where tickets should be created:")
    
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    try:
        msg = await bot.wait_for("message", check=check, timeout=30.0)
        try:
            category_id = int(msg.content.strip())
            category = ctx.guild.get_channel(category_id)
            
            if category and isinstance(category, discord.CategoryChannel):
                cursor.execute("""
                    INSERT INTO ticket_config (guild_id, category_id) 
                    VALUES (?, ?) 
                    ON CONFLICT(guild_id) DO UPDATE SET category_id = ?
                """, (ctx.guild.id, category_id, category_id))
                conn.commit()
                
                # Send the official ticket embed panel with the button
                embed = discord.Embed(
                    title="📩 Support Tickets",
                    description="Need help? Click the button below to open a private support ticket.",
                    color=discord.Color.blurple()
                )
                await ctx.send(embed=embed, view=TicketButtonView())
                await ctx.send(f"✅ Ticket system successfully configured under the **{category.name}** category.")
            else:
                await ctx.send("❌ Invalid Category ID. Make sure it's a category, not a simple text channel.")
        except ValueError:
            await ctx.send("❌ Invalid input. Please provide a numeric Category ID.")
    except asyncio.TimeoutError:
        await ctx.send("❌ Setup timed out. Please try running `!ticketconfig` again.")

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
        await member.timeout(None, reason="Unmuted via command")
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
            "`!mute <@member> <minutes> <reason>` : Temporarily mutes a member (handles `10` or `10m`).\n"
            "`!unmute <@member>` : Removes the timeout from a member.\n"
            "`!kick <@member> [reason]` : Kicks a member from the server.\n"
            "`!ban <@member> [reason]` : Permanently bans a member from the server."
        ),
        inline=False
    )

    embed.add_field(
        name="⚙️ Management & Utility Commands",
        value=(
            "`!lock` : Disables sending messages in the current channel.\n"
            "`!unlock` : Restores message permissions in the current channel.\n"
            "`!welcome` : Starts the interactive configuration for welcome messages.\n"
            "`!ticketconfig` : Sets up the automated ticket panel inside a chosen Category."
        ),
        inline=False
    )
    
    embed.set_footer(text="Automated anti-spam & anti-profanity active (3 warnings = Kick)")
    
    await ctx.send(embed=embed)

bot.run(os.getenv("DISCORD_TOKEN"))
