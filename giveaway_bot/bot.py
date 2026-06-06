"""
Giveaway Bot — fake giveaway bot where YOU choose the winner.

Setup:
  1. pip install -r requirements.txt
  2. Create a .env file with: DISCORD_TOKEN=your_token_here
  3. python bot.py

Commands (use in any channel):
  !gcreate <duration_seconds> <winners> <prize>
      — Start a giveaway. Users click the button to enter.
      — Example: !gcreate 60 1 Nitro Classic

  !gpick <message_id> @user
      — Manually pick a specific winner from the entrants.
      — Example: !gpick 1234567890 @someone

  !gend <message_id>
      — End a giveaway and pick a random winner from real entrants.

  !greroll <message_id>
      — Reroll a winner (random, from the same entrant pool).

  !glist
      — List all active giveaways in the server.

  !gentrants <message_id>
      — Show everyone who entered a giveaway.
"""

import os
import random
import asyncio
import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# giveaways[message_id] = {
#   "channel_id": int,
#   "prize": str,
#   "winners": int,
#   "host": int (user id),
#   "ends_at": datetime,
#   "entrants": set of user_ids,
#   "ended": bool,
#   "winner_ids": list of user_ids or None
# }
giveaways: dict[int, dict] = {}


# ─── Helpers ────────────────────────────────────────────────────────────────

def format_time(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s" if s else f"{m}m"
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    return f"{h}h {m}m" if m else f"{h}h"


def giveaway_embed(prize: str, host_id: int, ends_at: datetime,
                   winner_count: int, entrant_count: int,
                   ended: bool = False, winner_ids: list = None) -> discord.Embed:
    color = discord.Color.gold() if not ended else discord.Color.greyple()
    title = "🎉 GIVEAWAY ENDED 🎉" if ended else "🎉 GIVEAWAY 🎉"

    embed = discord.Embed(title=title, description=f"**{prize}**", color=color)

    if ended and winner_ids:
        winners_text = " ".join(f"<@{uid}>" for uid in winner_ids)
        embed.add_field(name="Winner(s)", value=winners_text, inline=False)
    elif ended:
        embed.add_field(name="Winner(s)", value="No valid entrants.", inline=False)
    else:
        embed.add_field(name="Ends", value=f"<t:{int(ends_at.timestamp())}:R>", inline=True)

    embed.add_field(name="Winners", value=str(winner_count), inline=True)
    embed.add_field(name="Entries", value=str(entrant_count), inline=True)
    embed.add_field(name="Hosted by", value=f"<@{host_id}>", inline=True)

    if not ended:
        embed.set_footer(text="Click the button below to enter!")
    else:
        embed.set_footer(text="Giveaway ended.")

    return embed


class GiveawayView(discord.ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="🎉 Enter Giveaway", style=discord.ButtonStyle.green,
                       custom_id="giveaway_enter")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = giveaways.get(self.message_id)
        if not g:
            await interaction.response.send_message("This giveaway no longer exists.", ephemeral=True)
            return
        if g["ended"]:
            await interaction.response.send_message("This giveaway has already ended.", ephemeral=True)
            return
        if datetime.utcnow() > g["ends_at"]:
            await interaction.response.send_message("This giveaway has already ended.", ephemeral=True)
            return

        uid = interaction.user.id
        if uid in g["entrants"]:
            g["entrants"].discard(uid)
            await interaction.response.send_message(
                "You left the giveaway. Click again to re-enter.", ephemeral=True)
        else:
            g["entrants"].add(uid)
            await interaction.response.send_message(
                f"✅ You entered for **{g['prize']}**! Good luck!", ephemeral=True)

        # Update the embed entry count
        channel = bot.get_channel(g["channel_id"])
        if channel:
            try:
                msg = await channel.fetch_message(self.message_id)
                embed = giveaway_embed(
                    g["prize"], g["host"], g["ends_at"],
                    g["winners"], len(g["entrants"])
                )
                await msg.edit(embed=embed, view=self)
            except Exception:
                pass


# ─── Auto-end task ───────────────────────────────────────────────────────────

@tasks.loop(seconds=5)
async def check_giveaways():
    now = datetime.utcnow()
    for msg_id, g in list(giveaways.items()):
        if not g["ended"] and now >= g["ends_at"]:
            await end_giveaway(msg_id, random_pick=True)


async def end_giveaway(msg_id: int, random_pick: bool = True,
                       forced_winners: list[int] = None):
    g = giveaways.get(msg_id)
    if not g or g["ended"]:
        return

    g["ended"] = True

    if forced_winners:
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
        msg = await channel.fetch_message(msg_id)
        embed = giveaway_embed(
            g["prize"], g["host"], g["ends_at"],
            g["winners"], len(g["entrants"]),
            ended=True, winner_ids=winner_ids
        )
        disabled_view = discord.ui.View()
        btn = discord.ui.Button(label="🎉 Giveaway Ended", style=discord.ButtonStyle.grey,
                                disabled=True)
        disabled_view.add_item(btn)
        await msg.edit(embed=embed, view=disabled_view)
    except Exception:
        pass

    if winner_ids:
        mentions = " ".join(f"<@{uid}>" for uid in winner_ids)
        await channel.send(
            f"🎉 Congratulations {mentions}! You won **{g['prize']}**!\n"
            f"[Jump to giveaway]({msg.jump_url})"
        )
    else:
        await channel.send(
            f"The giveaway for **{g['prize']}** ended with no valid entrants."
        )


# ─── Commands ────────────────────────────────────────────────────────────────

@bot.command(name="gcreate")
@commands.has_permissions(manage_guild=True)
async def gcreate(ctx, duration: int, winner_count: int, *, prize: str):
    """!gcreate <seconds> <winners> <prize>"""
    await ctx.message.delete()

    ends_at = datetime.utcnow() + timedelta(seconds=duration)
    embed = giveaway_embed(prize, ctx.author.id, ends_at, winner_count, 0)

    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="🎉 Enter Giveaway", style=discord.ButtonStyle.green,
        custom_id="giveaway_enter_placeholder"
    ))

    msg = await ctx.send(embed=embed)

    giveaways[msg.id] = {
        "channel_id": ctx.channel.id,
        "prize": prize,
        "winners": winner_count,
        "host": ctx.author.id,
        "ends_at": ends_at,
        "entrants": set(),
        "ended": False,
        "winner_ids": None,
    }

    real_view = GiveawayView(msg.id)
    embed = giveaway_embed(prize, ctx.author.id, ends_at, winner_count, 0)
    await msg.edit(embed=embed, view=real_view)

    await ctx.send(
        f"✅ Giveaway started! It ends in **{format_time(duration)}**.\n"
        f"Message ID: `{msg.id}` — use `!gpick {msg.id} @user` to force a winner.",
        delete_after=15
    )


@bot.command(name="gpick")
@commands.has_permissions(manage_guild=True)
async def gpick(ctx, message_id: int, member: discord.Member):
    """!gpick <message_id> @user  — Manually force this person to win."""
    g = giveaways.get(message_id)
    if not g:
        await ctx.send("❌ Giveaway not found. Check the message ID.", delete_after=10)
        return
    if g["ended"]:
        await ctx.send("❌ That giveaway has already ended.", delete_after=10)
        return

    # Add them to entrants if not already in
    g["entrants"].add(member.id)
    await end_giveaway(message_id, random_pick=False, forced_winners=[member.id])
    await ctx.send(
        f"✅ **{member.display_name}** has been manually selected as the winner of **{g['prize']}**!",
        delete_after=15
    )
    await ctx.message.delete()


@bot.command(name="gend")
@commands.has_permissions(manage_guild=True)
async def gend(ctx, message_id: int):
    """!gend <message_id>  — End a giveaway early with a random winner."""
    g = giveaways.get(message_id)
    if not g:
        await ctx.send("❌ Giveaway not found.", delete_after=10)
        return
    if g["ended"]:
        await ctx.send("❌ That giveaway has already ended.", delete_after=10)
        return

    await end_giveaway(message_id, random_pick=True)
    await ctx.send("✅ Giveaway ended.", delete_after=10)
    await ctx.message.delete()


@bot.command(name="greroll")
@commands.has_permissions(manage_guild=True)
async def greroll(ctx, message_id: int):
    """!greroll <message_id>  — Reroll a new random winner from the same entrant pool."""
    g = giveaways.get(message_id)
    if not g:
        await ctx.send("❌ Giveaway not found.", delete_after=10)
        return
    if not g["ended"]:
        await ctx.send("❌ That giveaway hasn't ended yet. Use `!gend` first.", delete_after=10)
        return
    if not g["entrants"]:
        await ctx.send("❌ No entrants to reroll from.", delete_after=10)
        return

    pool = list(g["entrants"])
    new_winners = random.sample(pool, min(g["winners"], len(pool)))
    g["winner_ids"] = new_winners

    mentions = " ".join(f"<@{uid}>" for uid in new_winners)
    await ctx.send(f"🎉 Reroll! New winner(s): {mentions} for **{g['prize']}**!")
    await ctx.message.delete()


@bot.command(name="gentrants")
@commands.has_permissions(manage_guild=True)
async def gentrants(ctx, message_id: int):
    """!gentrants <message_id>  — List everyone who entered."""
    g = giveaways.get(message_id)
    if not g:
        await ctx.send("❌ Giveaway not found.", delete_after=10)
        return

    if not g["entrants"]:
        await ctx.send(f"No one has entered the giveaway for **{g['prize']}** yet.", delete_after=15)
        return

    lines = [f"**Entrants for {g['prize']}** ({len(g['entrants'])} total):"]
    for uid in g["entrants"]:
        member = ctx.guild.get_member(uid)
        name = member.display_name if member else f"Unknown ({uid})"
        lines.append(f"• {name} — `{uid}`")

    await ctx.send("\n".join(lines), delete_after=30)
    await ctx.message.delete()


@bot.command(name="glist")
@commands.has_permissions(manage_guild=True)
async def glist(ctx):
    """!glist  — Show all active giveaways."""
    active = [(mid, g) for mid, g in giveaways.items() if not g["ended"]]
    if not active:
        await ctx.send("No active giveaways right now.", delete_after=10)
        return

    lines = [f"**Active Giveaways ({len(active)}):**"]
    for mid, g in active:
        channel = bot.get_channel(g["channel_id"])
        ch_name = channel.mention if channel else "Unknown channel"
        lines.append(
            f"• **{g['prize']}** — {ch_name} — {len(g['entrants'])} entrant(s) "
            f"— ends <t:{int(g['ends_at'].timestamp())}:R> — ID: `{mid}`"
        )

    await ctx.send("\n".join(lines), delete_after=30)
    await ctx.message.delete()


@bot.command(name="ghelp")
async def ghelp(ctx):
    """!ghelp  — Show all giveaway commands."""
    embed = discord.Embed(title="🎉 Giveaway Bot Commands", color=discord.Color.gold())
    embed.add_field(
        name="!gcreate <seconds> <winners> <prize>",
        value="Start a giveaway. Example: `!gcreate 120 1 Nitro Classic`",
        inline=False
    )
    embed.add_field(
        name="!gpick <message_id> @user",
        value="**Manually pick a specific winner.** The bot announces them as if they won randomly.",
        inline=False
    )
    embed.add_field(
        name="!gend <message_id>",
        value="End a giveaway early with a random winner from real entrants.",
        inline=False
    )
    embed.add_field(
        name="!greroll <message_id>",
        value="Pick a new random winner from the same entrant pool.",
        inline=False
    )
    embed.add_field(
        name="!gentrants <message_id>",
        value="List everyone who entered a giveaway.",
        inline=False
    )
    embed.add_field(
        name="!glist",
        value="Show all currently active giveaways.",
        inline=False
    )
    embed.set_footer(text="Most commands require Manage Server permission.")
    await ctx.send(embed=embed)


# ─── Events ──────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Giveaway bot is online. Use !ghelp to see commands.")
    check_giveaways.start()


@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data.get("custom_id", "")
        if custom_id == "giveaway_enter":
            # Find which giveaway this message belongs to
            msg_id = interaction.message.id
            if msg_id in giveaways:
                view = GiveawayView(msg_id)
                await view.enter.callback(view, interaction)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You need **Manage Server** permission to use this command.", delete_after=8)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument. Use `!ghelp` to see usage.", delete_after=8)
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Invalid argument. Use `!ghelp` to see usage.", delete_after=8)
    else:
        raise error


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: No DISCORD_TOKEN found.")
        print("Create a .env file with: DISCORD_TOKEN=your_token_here")
        exit(1)
    bot.run(TOKEN)
