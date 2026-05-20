import discord
from discord.ext import commands
import asyncio
import re
import sqlite3
from datetime import datetime, timedelta

# Base de données
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

# Limitation du spam
user_messages = {}

INSULTES = [ r"\bidiot\b", r"\bcon\b" ] # Écourtée pour l'exemple

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot connecté en tant que : {bot.user}")

@bot.event
async def on_message(message):
    if message.author == bot.user or not message.guild or message.author.bot:
        return

    if message.author.guild_permissions.manage_messages:
        await bot.process_commands(message)
        return

    user_id = message.author.id
    current_time = datetime.now()

    # --- Détection de spam ---
    if user_id not in user_messages:
        user_messages[user_id] = []
    user_messages[user_id].append(current_time)
    user_messages[user_id] = [t for t in user_messages[user_id] if (current_time - t).total_seconds() <= 15]

    if len(user_messages[user_id]) > 4:
        user_messages[user_id] = []
        await handle_mute_and_warn(message, "Spam détecté (4 messages en 15 sec)")
        return

    # --- Détection automatique des insultes ---
    contenu = message.content.lower()
    for insulte in INSULTES:
        if re.search(insulte, contenu):
            await handle_mute_and_warn(message, "Propos inappropriés")
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
            await message.author.kick(reason=f"3 avertissements : {reason}")
            await message.channel.send(f"❌ {message.author.mention} a été **kick** après 3 avertissements.")
            cursor.execute("DELETE FROM warnings WHERE user_id = ?", (user_id,))
            conn.commit()
        else:
            await message.author.timeout(timedelta(minutes=10), reason=reason)
            await message.channel.send(
                f"⚠️ {message.author.mention} a été exclu temporairement (10 min). Raison : {reason} ({warnings_count}/3)"
            )
    except discord.Forbidden:
        print(f"Permissions insuffisantes pour agir contre {message.author.name}")

# --- Commandes administratives ---

@bot.command(name="mute")
@commands.has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, minutes: int = 10, *, reason="Aucune raison"):
    try:
        await member.timeout(timedelta(minutes=minutes), reason=reason)
        await ctx.send(f"✅ {member.mention} a été réduit au silence pour {minutes} minutes.")
    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas les permissions pour exclure ce membre.")

@bot.command(name="unmute")
@commands.has_permissions(moderate_members=True)
async def unmute(ctx, member: discord.Member):
    try:
        await member.timeout(None)
        await ctx.send(f"✅ {member.mention} n'est plus réduit au silence.")
    except discord.Forbidden:
        await ctx.send("❌ Impossible d'annuler l'exclusion.")

# --- Nouvelle commande : !cmds ---

@bot.command(name="cmds")
async def cmds(ctx):
    """Affiche la liste de toutes les commandes disponibles sur le serveur."""
    
    # Création de l'embed
    embed = discord.Embed(
        title="📜 Liste des commandes du serveur",
        description="Voici les commandes que vous pouvez utiliser avec le préfixe `!`",
        color=discord.Color.blue()
    )
    
    # Section Commandes Membres (Tout le monde peut les voir)
    embed.add_field(
        name="👥 Commandes Générales",
        value="`!cmds` : Affiche cette liste d'aide.",
        inline=False
    )
    
    # Section Modération (Visible par tout le monde dans l'aide, mais exécutable uniquement par les modos)
    embed.add_field(
        name="🛡️ Commandes de Modération",
        value=(
            "`!mute <@membre> [minutes] [raison]` : Met un membre en exclusion temporaire (par défaut 10 min).\n"
            "`!unmute <@membre>` : Retire l'exclusion temporaire d'un membre."
        ),
        inline=False
    )
    
    # Information sur le système automatique en bas de l'embed
    embed.set_footer(text="Système anti-spam et anti-insultes actif (3 avertissements = Kick)")
    
    # Envoi de l'embed dans le salon où la commande a été tapée
    await ctx.send(embed=embed)


bot.run("MTUwNTk5ODk1NDUxNTY2MDg3MQ.Gg31m8.Wx5-LTt-47b4CdJyAhtB_tHQwOGgRpLMgKJGNY")
