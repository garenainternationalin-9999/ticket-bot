from tortoise import fields, models

class TicketPanel(models.Model):
    id = fields.IntField(pk=True)
    guild_id = fields.BigIntField()
    channel_id = fields.BigIntField(null=True)
    title = fields.CharField(max_length=255, default="Support Ticket")
    description = fields.TextField(default="Click the button below to contact support.")
    
    # Image URLs (Links only, no local storage)
    banner_url = fields.CharField(max_length=1024, null=True, default="")
    thumbnail_url = fields.CharField(max_length=1024, null=True, default="")
    
    # Button Customization
    button_text = fields.CharField(max_length=50, default="Open Ticket")
    button_color = fields.CharField(max_length=20, default="blurple") # blurple, gray, green, red
    button_emoji = fields.CharField(max_length=50, default="📩")

    # Logic
    staff_roles = fields.JSONField(default=list)      
    dropdown_options = fields.JSONField(default=list) 
    
    naming_format = fields.CharField(max_length=50, default="ticket-{username}")
    created_at = fields.DatetimeField(auto_now_add=True)

class Ticket(models.Model):
    id = fields.IntField(pk=True)
    panel = fields.ForeignKeyField('models.TicketPanel', related_name='tickets')
    channel_id = fields.BigIntField()
    creator_id = fields.BigIntField()
    status = fields.CharField(max_length=20, default="open") 
    claimed_by = fields.BigIntField(null=True)
    category_selected = fields.CharField(max_length=100, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)