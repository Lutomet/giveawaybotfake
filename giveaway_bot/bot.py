"""
Giveaway Bot — embedslash command giveaway bot where YOU choose the winner.

Setup:
  1. pip install -r requirements.txt
  2. Create a .env file with: DISCORD_TOKEN=your_token_here
  3. python bot.py

Slash Commands (owner-only):
  /gcreate duration winners prize  — Start a giveaway
  /gpick message_id user           — Force a specific winner (looks random)
  /gend message_id                 — End early with a random winner
  /greroll message_id              — Reroll a new random winner
  /gentrants message_id            — See everyone who entered
  /glist                           — List all active giveaways
"""

import os
import random
import discord
from discord import app_commands
from discord.ext import tasks
from datetime import datetime, timedelta

TOKEN = os.environ.get("DISCORD_TOKEN")
OWNER_ID = 1435693467421376551


# ─── Bot setup ───────────────────────────────────────────────────────────────

intents = discord.Intents.default()

class GiveawayBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        print("Slash commands synced.")

bot = GiveawayBot()

# giveaways[message_id] = { channel_id, prize, winners, host, ends_at,
#                            entrants (set), ended, winner_ids }
giveaways: dict[int, dict] = {}


# ─── Owner check ─────────────────────────────────────────────────────────────

def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == OWNER_ID

def owner_only(interaction: discord.Interaction) -> bool:
    if interaction.user.id != OWNER_ID:
        raise app_commands.CheckFailure("Only the bot owner can use this command.")
    return True


# ─── Embed builder ───────────────────────────────────────────────────────────

BLUE = 0x5865F2

def build_embed(g: dict, ended: bool = False) -> discord.Embed:
    ends_at: datetime = g["ends_at"]
    ts = int(ends_at.timestamp())

    if ended:
        if g.get("winner_ids"):
            winners_text = " ".join(f"<@{uid}>" for uid in g["winner_ids"])
            body = (
                f"**Winner(s):** {winners_text}\n"
                f"**Hosted by:** <@{g['host']}>\n"
                f"**Entries:** {len(g['entrants'])}\n"
                f"**Winners:** {g['winners']}"
            )
        else:
            body = (
                f"**Winner(s):** No valid entrants.\n"
                f"**Hosted by:** <@{g['host']}>\n"
                f"**Entries:** {len(g['entrants'])}\n"
                f"**Winners:** {g['winners']}"
            )
        embed = discord.Embed(title=g["prize"], description=body, color=BLUE)
        embed.set_footer(text="Ended at")
        embed.timestamp = ends_at
    else:
        body = (
            f"**Ends:** <t:{ts}:R> (<t:{ts}:f>)\n"
            f"**Hosted by:** <@{g['host']}>\n"
            f"**Entries:** {len(g['entrants'])}\n"
            f"**Winners:** {g['winners']}"
        )
        embed = discord.Embed(title=g["prize"], description=body, color=BLUE)
        embed.timestamp = ends_at

    return embed


# ─── Button view ─────────────────────────────────────────────────────────────

class LeaveView(discord.ui.View):
    def __init__(self, message_id: int, user_id: int):
        super().__init__(timeout=60)
        self.message_id = message_id
        self.user_id = user_id

    @discord.ui.button(label="Leave Giveaway", style=discord.ButtonStyle.danger)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.defer()
            return

        g = giveaways.get(self.message_id)
        if g and not g["ended"]:
            g["entrants"].discard(self.user_id)
            try:
                channel = bot.get_channel(g["channel_id"])
                if channel:
                    msg = await channel.fetch_message(self.message_id)
                    view = GiveawayView(self.message_id)
                    await msg.edit(embed=build_embed(g), view=view)
            except Exception:
                pass

        self.stop()
        await interaction.response.edit_message(
            content="You have left the giveaway.", view=None)


class GiveawayView(discord.ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="🎉", style=discord.ButtonStyle.blurple,
                       custom_id="giveaway_enter")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = giveaways.get(self.message_id)
        if not g or g["ended"] or datetime.utcnow() > g["ends_at"]:
            await interaction.response.send_message(
                "This giveaway has already ended.", ephemeral=True)
            return

        uid = interaction.user.id

        if uid in g["entrants"]:
            # Already entered — show "already entered" message with Leave button
            leave_view = LeaveView(self.message_id, uid)
            await interaction.response.send_message(
                "You have already entered this giveaway!",
                view=leave_view,
                ephemeral=True
            )
            return

        # New entry — add silently and update the embed
        g["entrants"].add(uid)
        await interaction.response.defer()

        try:
            await interaction.message.edit(embed=build_embed(g), view=self)
        except Exception:
            pass


class EndedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        btn = discord.ui.Button(
            label="🎉", style=discord.ButtonStyle.grey, disabled=True)
        self.add_item(btn)


# ─── Auto-end task ───────────────────────────────────────────────────────────

@tasks.loop(seconds=5)
async def check_giveaways():
    now = datetime.utcnow()
    for msg_id, g in list(giveaways.items()):
        if not g["ended"] and now >= g["ends_at"]:
            await conclude_giveaway(msg_id, random_pick=True)


async def conclude_giveaway(msg_id: int, random_pick: bool = True,
                             forced_winners: list[int] | None = None):
    g = giveaways.get(msg_id)
    if not g or g["ended"]:
        return
    g["ended"] = True

    if g.get("forced_winner"):
        winner_ids = [g["forced_winner"]]
    elif forced_winners:
        winner_ids = forced_winners
    elif random_pick and g["entrants"]:
        pool = list(g["entrants"])
        count = min(g["winners"], len(pool))
        winner_ids = random.sample(pool, count)
    else:
        winner_ids = []

    g["winner_ids"] = winner_ids

    channel = bot.get_channel(g["channel_id"])
    if not channel:
        return

    try:
        discord_msg = await channel.fetch_message(msg_id)
        await discord_msg.edit(embed=build_embed(g, ended=True), view=EndedView())
    except Exception:
        pass

    if winner_ids:
        mentions = " ".join(f"<@{uid}>" for uid in winner_ids)
        prize = g["prize"]
        await channel.send(
            f"Congratulations {mentions}! You won the **{prize}**!"
        )
    else:
        await channel.send(
            f"The giveaway for **{g['prize']}** ended with no valid entrants."
        )


# ─── Slash Commands ───────────────────────────────────────────────────────────

@bot.tree.command(name="gcreate", description="Start a giveaway")
@app_commands.check(owner_only)
@app_commands.describe(
    duration="Duration in seconds (e.g. 86400 = 1 day)",
    winners="Number of winners",
    prize="What are you giving away?"
)
async def gcreate(interaction: discord.Interaction, duration: int, winners: int, prize: str):
    await interaction.response.defer(ephemeral=True)

    ends_at = datetime.utcnow() + timedelta(seconds=duration)
    g = {
        "channel_id": interaction.channel_id,
        "prize": prize,
        "winners": winners,
        "host": interaction.user.id,
        "ends_at": ends_at,
        "entrants": set(),
        "ended": False,
        "winner_ids": None,
    }

    # Send a placeholder to get the message ID, then update with the real view
    placeholder = await interaction.channel.send("Starting giveaway…")
    g_id = placeholder.id
    giveaways[g_id] = g

    view = GiveawayView(g_id)
    await placeholder.edit(content=None, embed=build_embed(g), view=view)

    await interaction.followup.send(
        f"✅ Giveaway started! Message ID: `{g_id}`\n"
        f"Use `/gpick message_id:` `{g_id}` `user: @someone` to force a winner.",
        ephemeral=True
    )


@bot.tree.command(name="gpick", description="Manually force a specific person to win")
@app_commands.check(owner_only)
@app_commands.describe(
    message_id="The giveaway message ID",
    user="The user you want to win"
)
async def gpick(interaction: discord.Interaction, message_id: str, user: discord.Member):
    await interaction.response.defer(ephemeral=True)

    try:
        msg_id = int(message_id)
    except ValueError:
        await interaction.followup.send("❌ Invalid message ID.", ephemeral=True)
        return

    g = giveaways.get(msg_id)
    if not g:
        await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
        return
    if g["ended"]:
        await interaction.followup.send("❌ That giveaway has already ended.", ephemeral=True)
        return

    g["entrants"].add(user.id)
    g["forced_winner"] = user.id
    await interaction.followup.send(
        f"✅ **{user.display_name}** is queued as the winner of **{g['prize']}**. "
        f"They will be announced when the giveaway ends.",
        ephemeral=True
    )


@bot.tree.command(name="gend", description="End a giveaway early with a random winner")
@app_commands.check(owner_only)
@app_commands.describe(message_id="The giveaway message ID")
async def gend(interaction: discord.Interaction, message_id: str):
    await interaction.response.defer(ephemeral=True)

    try:
        msg_id = int(message_id)
    except ValueError:
        await interaction.followup.send("❌ Invalid message ID.", ephemeral=True)
        return

    g = giveaways.get(msg_id)
    if not g:
        await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
        return
    if g["ended"]:
        await interaction.followup.send("❌ Already ended.", ephemeral=True)
        return

    await conclude_giveaway(msg_id, random_pick=True)
    await interaction.followup.send("✅ Giveaway ended.", ephemeral=True)


@bot.tree.command(name="greroll", description="Pick a new random winner from the same entrant pool")
@app_commands.check(owner_only)
@app_commands.describe(message_id="The giveaway message ID")
async def greroll(interaction: discord.Interaction, message_id: str):
    await interaction.response.defer(ephemeral=True)

    try:
        msg_id = int(message_id)
    except ValueError:
        await interaction.followup.send("❌ Invalid message ID.", ephemeral=True)
        return

    g = giveaways.get(msg_id)
    if not g:
        await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
        return
    if not g["ended"]:
        await interaction.followup.send("❌ Giveaway hasn't ended yet.", ephemeral=True)
        return
    if not g["entrants"]:
        await interaction.followup.send("❌ No entrants to reroll from.", ephemeral=True)
        return

    pool = list(g["entrants"])
    new_winners = random.sample(pool, min(g["winners"], len(pool)))
    g["winner_ids"] = new_winners

    mentions = " ".join(f"<@{uid}>" for uid in new_winners)
    channel = bot.get_channel(g["channel_id"])
    if channel:
        await channel.send(f"🎉 Reroll! New winner(s): {mentions} for **{g['prize']}**!")

    await interaction.followup.send(f"✅ Rerolled. New winner(s): {mentions}", ephemeral=True)


@bot.tree.command(name="gentrants", description="See everyone who entered a giveaway")
@app_commands.check(owner_only)
@app_commands.describe(message_id="The giveaway message ID")
async def gentrants(interaction: discord.Interaction, message_id: str):
    await interaction.response.defer(ephemeral=True)

    try:
        msg_id = int(message_id)
    except ValueError:
        await interaction.followup.send("❌ Invalid message ID.", ephemeral=True)
        return

    g = giveaways.get(msg_id)
    if not g:
        await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
        return

    if not g["entrants"]:
        await interaction.followup.send(
            f"No one has entered the giveaway for **{g['prize']}** yet.", ephemeral=True)
        return

    lines = [f"**Entrants for {g['prize']}** ({len(g['entrants'])} total):"]
    for uid in g["entrants"]:
        member = interaction.guild.get_member(uid) if interaction.guild else None
        name = member.display_name if member else f"Unknown"
        lines.append(f"• {name} — <@{uid}>")

    await interaction.followup.send("\n".join(lines), ephemeral=True)


@bot.tree.command(name="glist", description="List all active giveaways")
@app_commands.check(owner_only)
async def glist(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    active = [(mid, g) for mid, g in giveaways.items() if not g["ended"]]
    if not active:
        await interaction.followup.send("No active giveaways right now.", ephemeral=True)
        return

    lines = [f"**Active Giveaways ({len(active)}):**"]
    for mid, g in active:
        channel = bot.get_channel(g["channel_id"])
        ch = channel.mention if channel else "Unknown"
        lines.append(
            f"• **{g['prize']}** — {ch} — {len(g['entrants'])} entrant(s) "
            f"— ends <t:{int(g['ends_at'].timestamp())}:R> — ID: `{mid}`"
        )

    await interaction.followup.send("\n".join(lines), ephemeral=True)


# ─── Error handler ────────────────────────────────────────────────────────────

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True)
    else:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"❌ An error occurred: {error}", ephemeral=True)


# ─── Events ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Slash commands synced. Use /gcreate to start a giveaway.")
    check_giveaways.start()


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: No DISCORD_TOKEN found.")
        print("Create a .env file with: DISCORD_TOKEN=your_token_here")
        exit(1)
    bot.run(TOKEN)
