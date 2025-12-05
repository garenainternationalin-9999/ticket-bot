import discord
from discord.ext import commands
from models import TicketPanel, Ticket
import asyncio
import io

# --- Configuration ---
BUTTON_STYLES = {
    "blurple": discord.ButtonStyle.blurple,
    "gray": discord.ButtonStyle.gray,
    "green": discord.ButtonStyle.success,
    "red": discord.ButtonStyle.danger
}

# --- Temporary Storage for Selections ---
# Stores {user_id: "Category Name"}
# This allows the Button to know what the User selected in the Dropdown.
user_selections = {}

# --- 1. The Dropdown (Select Menu) ---
# --- Updated TicketLauncher & PanelSelect to support Custom Emojis ---

class PanelSelect(discord.ui.Select):
    def __init__(self, panel_id, options):
        # Parse options to support Custom Emojis
        discord_options = []
        for o in options:
            # Smart Emoji Converter
            emoji = o['emoji']
            if "<:" in emoji or "<a:" in emoji:
                 emoji = discord.PartialEmoji.from_str(emoji)
            
            discord_options.append(discord.SelectOption(label=o['label'], emoji=emoji, value=o['label']))

        super().__init__(
            placeholder="Select a support category...",
            min_values=1, 
            max_values=1, 
            options=discord_options,
            custom_id=f"ticket:select:{panel_id}"
        )

    async def callback(self, interaction: discord.Interaction):
        # 1. Save selection
        user_selections[interaction.user.id] = self.values[0]
        # 2. Ephemeral confirm
        await interaction.response.send_message(
            f"✅ Category set to **{self.values[0]}**.\n👉 Now click the **Create Ticket** button below to proceed.", 
            ephemeral=True
        )

class TicketLauncher(discord.ui.View):
    def __init__(self, panel: TicketPanel):
        super().__init__(timeout=None)
        self.panel_id = panel.id
        
        # Add Dropdown if options exist
        if panel.dropdown_options and len(panel.dropdown_options) > 0:
            self.add_item(PanelSelect(panel.id, panel.dropdown_options))
        
        # Smart Button Emoji Converter
        emoji = panel.button_emoji
        if "<:" in emoji or "<a:" in emoji:
            emoji = discord.PartialEmoji.from_str(emoji)

        style = BUTTON_STYLES.get(panel.button_color, discord.ButtonStyle.blurple)
        self.add_item(discord.ui.Button(
            label=panel.button_text, 
            style=style, 
            custom_id=f"ticket:btn:{panel.id}",
            emoji=emoji
        ))


# --- 3. Ticket Controls (Inside the Ticket Channel) ---
class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.success, custom_id="ticket:claim", emoji="🙋‍♂️")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await Ticket.filter(channel_id=interaction.channel.id).first().prefetch_related('panel')
        if not ticket: return

        # Permission Check: Only Staff Roles
        user_role_ids = [r.id for r in interaction.user.roles]
        if not any(r_id in ticket.panel.staff_roles for r_id in user_role_ids) and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Only assigned staff can claim tickets.", ephemeral=True)

        if ticket.claimed_by:
            return await interaction.response.send_message(f"❌ Already claimed by <@{ticket.claimed_by}>", ephemeral=True)

        ticket.claimed_by = interaction.user.id
        await ticket.save()
        
        # Update Channel Topic
        await interaction.channel.edit(topic=f"Ticket Claimed by: {interaction.user.name}")
        
        embed = discord.Embed(description=f"✅ **Ticket successfully claimed by {interaction.user.mention}**", color=0x4ade80)
        await interaction.response.send_message(embed=embed)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket:close", emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await Ticket.filter(channel_id=interaction.channel.id).first().prefetch_related('panel')
        if not ticket: return

        # Permission Check: Staff OR Creator
        user_role_ids = [r.id for r in interaction.user.roles]
        is_staff = any(r_id in ticket.panel.staff_roles for r_id in user_role_ids) or interaction.user.guild_permissions.administrator
        
        if interaction.user.id != ticket.creator_id and not is_staff:
            return await interaction.response.send_message("❌ You do not have permission to close this ticket.", ephemeral=True)

        await interaction.response.send_message("🛑 **Closing ticket in 5 seconds...**", ephemeral=True)
        await asyncio.sleep(5)
        
        # Generate Transcript
        messages = [f"{m.created_at.strftime('%Y-%m-%d %H:%M')} - {m.author.name}: {m.content}" async for m in interaction.channel.history(limit=500, oldest_first=True)]
        transcript_text = "\n".join(messages)
        file = discord.File(io.StringIO(transcript_text), filename=f"transcript-{ticket.id}.txt")
        
        # DM Transcript to Creator
        try:
            creator = await interaction.client.fetch_user(ticket.creator_id)
            embed = discord.Embed(title="Ticket Closed", description=f"Your ticket in **{interaction.guild.name}** has been closed.", color=0xef4444)
            embed.add_field(name="Category", value=ticket.category_selected or "General")
            embed.set_footer(text="Neutron Premium Transcripts")
            await creator.send(embed=embed, file=file)
        except:
            pass 

        await interaction.channel.delete()
        ticket.status = "closed"
        await ticket.save()

# --- 4. Main Bot Class ---
class TicketBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True 
        intents.message_content = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, help_command=None)

    async def setup_hook(self):
        self.add_view(TicketControls())
        try:
            panels = await TicketPanel.all()
            for panel in panels:
                self.add_view(TicketLauncher(panel))
        except Exception as e:
            print(f"⚠️ Error loading panels: {e}")

    async def on_ready(self):
        print(f"✅ Neutron Bot Ready | Connected to {len(self.guilds)} servers")

    async def on_interaction(self, interaction: discord.Interaction):
        # Handle BUTTON clicks
        if interaction.type == discord.InteractionType.component:
            cid = interaction.data.get('custom_id', '')
            if cid.startswith("ticket:btn:"):
                panel_id = int(cid.split(":")[-1])
                
                # Check if user selected something in the dropdown previously
                # If yes, use it. If no, use "General Support".
                category = user_selections.pop(interaction.user.id, "General Support")
                
                await self.create_ticket(interaction, panel_id, category)
        
        await super().on_interaction(interaction)

    async def create_ticket(self, interaction: discord.Interaction, panel_id: int, category_name: str):
        panel = await TicketPanel.get_or_none(id=panel_id)
        if not panel: return await interaction.response.send_message("❌ Panel configuration not found.", ephemeral=True)

        guild = interaction.guild
        
        # Spam Check
        existing = await Ticket.filter(creator_id=interaction.user.id, status="open", panel_id=panel.id).exists()
        if existing:
             return await interaction.response.send_message("❌ You already have an open ticket.", ephemeral=True)

        # Category Setup
        category = discord.utils.get(guild.categories, name="Neutron Tickets")
        if not category:
            overwrites = {guild.default_role: discord.PermissionOverwrite(read_messages=False), guild.me: discord.PermissionOverwrite(read_messages=True, manage_channels=True)}
            category = await guild.create_category("Neutron Tickets", overwrites=overwrites)

        # Permissions (Strict Visibility)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, manage_channels=True, manage_permissions=True)
        }
        
        # Add Staff Roles
        for role_id in panel.staff_roles:
            role = guild.get_role(role_id)
            if role: overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        # Create Channel
        channel_name = f"{category_name.lower().replace(' ', '-')}-{interaction.user.name}"
        try:
            chan = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
        except Exception as e:
            return await interaction.response.send_message(f"❌ Error creating channel: {e}", ephemeral=True)

        # Database Save
        await Ticket.create(panel=panel, channel_id=chan.id, creator_id=interaction.user.id, category_selected=category_name)

        # Premium Welcome Embed
        embed = discord.Embed(
            title=f"{panel.button_emoji} {category_name}",
            description=f"Hello {interaction.user.mention},\n\nThanks for reaching out! Our staff team has been notified.\n\n**Category:** {category_name}\n**Status:** 🟢 Open\n\n⚠️ *Unclaimed tickets are automatically deleted in 24 hours.*",
            color=0x5865F2
        )
        if panel.thumbnail_url: embed.set_thumbnail(url=panel.thumbnail_url)
        embed.set_footer(text=f"Neutron Premium • {guild.name}")
        
        await chan.send(content=f"{interaction.user.mention}", embed=embed, view=TicketControls())
        
        # Confirm Creation
        await interaction.response.send_message(f"✅ **Ticket Created!** Access it here: {chan.mention}", ephemeral=True)

bot_instance = TicketBot()