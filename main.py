import os
import asyncio
import httpx
import discord
from fastapi import FastAPI, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from tortoise.contrib.fastapi import register_tortoise
from dotenv import load_dotenv

from bot import bot_instance, TicketLauncher
from models import TicketPanel

load_dotenv()

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "CHANGEME"), max_age=2592000) # 30 Days Login
templates = Jinja2Templates(directory="templates")

# --- Helpers ---
async def get_current_user(request: Request):
    return request.session.get("user")

# --- Auth ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if request.session.get("user"): return RedirectResponse("/dashboard")
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/auth/login")
async def login():
    scope = "identify guilds"
    url = f"https://discord.com/api/oauth2/authorize?client_id={os.getenv('CLIENT_ID')}&redirect_uri={os.getenv('REDIRECT_URI')}&response_type=code&scope={scope}"
    return RedirectResponse(url)

@app.get("/auth/callback")
async def auth_callback(code: str, request: Request):
    async with httpx.AsyncClient() as client:
        data = {"client_id": os.getenv("CLIENT_ID"), "client_secret": os.getenv("CLIENT_SECRET"), "grant_type": "authorization_code", "code": code, "redirect_uri": os.getenv("REDIRECT_URI")}
        r = await client.post("https://discord.com/api/oauth2/token", data=data)
        if r.status_code != 200: return HTMLResponse("Discord Auth Failed", 400)
        tokens = r.json()
        
        u = await client.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
        request.session["user"] = u.json()
        request.session["token"] = tokens["access_token"]
    return RedirectResponse("/dashboard")

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")

# --- Dashboard ---
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user=Depends(get_current_user)):
    if not user: return RedirectResponse("/")
    
    async with httpx.AsyncClient() as client:
        r = await client.get("https://discord.com/api/users/@me/guilds", headers={"Authorization": f"Bearer {request.session['token']}"})
        if r.status_code != 200: 
            request.session.clear()
            return RedirectResponse("/")
        user_guilds = r.json()

    if not bot_instance.is_ready():
        await bot_instance.wait_until_ready()
    
    bot_guild_ids = {str(g.id) for g in bot_instance.guilds}
    
    display_guilds = []
    for g in user_guilds:
        if (int(g["permissions"]) & 0x20) == 0x20:
            g["bot_in"] = g["id"] in bot_guild_ids
            display_guilds.append(g)

    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": user, "guilds": display_guilds,
        "invite_url": f"https://discord.com/api/oauth2/authorize?client_id={os.getenv('CLIENT_ID')}&permissions=8&scope=bot"
    })

@app.get("/dashboard/{guild_id}", response_class=HTMLResponse)
async def manage_guild(guild_id: int, request: Request, user=Depends(get_current_user)):
    if not user: return RedirectResponse("/")
    guild = bot_instance.get_guild(guild_id)
    if not guild: return HTMLResponse("Bot not in server", 404)

    panels = await TicketPanel.filter(guild_id=guild_id)
    channels = [{"id": c.id, "name": c.name} for c in guild.text_channels]
    
    # --- FIXED EMOJI FETCHING ---
    bot_emojis = []
    # 1. Fetch Application Emojis (From Developer Portal)
    try:
        app_emojis = await bot_instance.fetch_application_emojis()
        bot_emojis.extend(app_emojis)
    except Exception as e:
        print(f"⚠️ Could not fetch app emojis: {e}")

    # 2. Fetch Guild Emojis (From Servers)
    bot_emojis.extend(bot_instance.emojis)
    # ----------------------------

    roles = []
    for r in guild.roles:
        if not r.is_default():
            color = str(r.color) if r.color.value != 0 else "#99aab5"
            roles.append({"id": r.id, "name": r.name, "color": color})

    return templates.TemplateResponse("panel_editor.html", {
        "request": request, "user": user, "guild": guild, 
        "panels": panels, "channels": channels, "roles": roles,
        "bot_emojis": bot_emojis
    })

@app.post("/dashboard/{guild_id}/create_panel")
async def create_panel(
    guild_id: int, request: Request,
    title: str = Form(...), description: str = Form(...), channel_id: int = Form(...),
    button_text: str = Form("Open Ticket"), button_color: str = Form("blurple"), button_emoji: str = Form("📩"),
    banner_url: str = Form(""), thumbnail_url: str = Form(""),
    user=Depends(get_current_user)
):
    if not user: return RedirectResponse("/")
    form_data = await request.form()
    
    staff_roles = [int(r) for r in form_data.getlist("staff_roles")]
    labels = form_data.getlist("dd_label")
    emojis = form_data.getlist("dd_emoji")
    
    dropdown_options = []
    for l, e in zip(labels, emojis):
        if l.strip(): dropdown_options.append({"label": l.strip(), "emoji": e.strip() or "🎫"})

    await TicketPanel.create(
        guild_id=guild_id, title=title, description=description, channel_id=channel_id,
        banner_url=banner_url, thumbnail_url=thumbnail_url,
        staff_roles=staff_roles, dropdown_options=dropdown_options,
        button_text=button_text, button_color=button_color, button_emoji=button_emoji
    )
    return RedirectResponse(f"/dashboard/{guild_id}", 303)

@app.post("/dashboard/{guild_id}/publish/{panel_id}")
async def publish_panel(guild_id: int, panel_id: int, request: Request):
    panel = await TicketPanel.get(id=panel_id)
    guild = bot_instance.get_guild(guild_id)
    if not guild: return "Guild not found"
    channel = guild.get_channel(panel.channel_id)
    if not channel: return "Channel not found"

    embed = discord.Embed(title=panel.title, description=panel.description, color=0x2b2d31)
    if panel.banner_url: embed.set_image(url=panel.banner_url)
    if panel.thumbnail_url: embed.set_thumbnail(url=panel.thumbnail_url)
    embed.set_footer(text="Neutron Premium")
    
    view = TicketLauncher(panel)
    await channel.send(embed=embed, view=view)
    return RedirectResponse(f"/dashboard/{guild_id}", 303)

@app.post("/dashboard/{guild_id}/delete/{panel_id}")
async def delete_panel(guild_id: int, panel_id: int):
    await TicketPanel.filter(id=panel_id).delete()
    return RedirectResponse(f"/dashboard/{guild_id}", 303)

register_tortoise(app, db_url=os.getenv("DATABASE_URL"), modules={"models": ["models"]}, generate_schemas=True)

@app.on_event("startup")
async def startup():
    asyncio.create_task(bot_instance.start(os.getenv("DISCORD_TOKEN")))