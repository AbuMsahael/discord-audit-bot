import discord
from discord.ext import commands, tasks
import asyncio
import json
import os
from datetime import datetime, timedelta
import sqlite3

# إعداد البوت
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# إعدادات - سيتم أخذها من Railway
LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID'))  # سيتم إعداده في Railway
GUILD_ID = int(os.getenv('GUILD_ID'))  # سيتم إعداده في Railway  
BOT_TOKEN = os.getenv('BOT_TOKEN')  # سيتم إعداده في Railway

# إنشاء قاعدة البيانات
def init_db():
    conn = sqlite3.connect('audit_logs.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            action_type TEXT,
            target_id INTEGER,
            target_name TEXT,
            user_id INTEGER,
            user_name TEXT,
            reason TEXT,
            timestamp TEXT,
            changes TEXT,
            processed BOOLEAN DEFAULT FALSE
        )
    ''')
    
    conn.commit()
    conn.close()

def save_audit_log(guild_id, action_type, target_id, target_name, user_id, user_name, reason, timestamp, changes):
    conn = sqlite3.connect('audit_logs.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO audit_logs (guild_id, action_type, target_id, target_name, user_id, user_name, reason, timestamp, changes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (guild_id, action_type, target_id, target_name, user_id, user_name, reason, timestamp, json.dumps(changes)))
    
    conn.commit()
    conn.close()

@bot.event
async def on_ready():
    print(f'البوت جاهز! {bot.user}')
    print('بدء مراقبة السيرفر...')
    init_db()
    check_old_logs.start()  # بدء فحص اللوقات القديمة

@tasks.loop(minutes=5)  # فحص كل 5 دقائق
async def check_old_logs():
    """فحص اللوقات القديمة من Discord Audit Log"""
    try:
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return

        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            return

        # فحص آخر 100 عملية في الـ audit log
        async for entry in guild.audit_logs(limit=100):
            # التحقق من أن العملية لم تتم معالجتها من قبل
            if await is_log_processed(entry.id):
                continue

            await process_audit_entry(entry, log_channel, guild)
            
    except Exception as e:
        print(f"خطأ في فحص اللوقات القديمة: {e}")

async def is_log_processed(entry_id):
    """التحقق من معالجة اللوق مسبقاً"""
    conn = sqlite3.connect('audit_logs.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT id FROM audit_logs WHERE target_id = ? AND processed = TRUE', (entry_id,))
    result = cursor.fetchone()
    
    conn.close()
    return result is not None

async def process_audit_entry(entry, log_channel, guild):
    """معالجة عنصر من audit log"""
    try:
        changes_dict = {}
        
        # معالجة التغييرات المختلفة
        if entry.action in [discord.AuditLogAction.channel_create, 
                           discord.AuditLogAction.channel_update, 
                           discord.AuditLogAction.channel_delete]:
            await handle_channel_changes(entry, log_channel, changes_dict)
            
        elif entry.action in [discord.AuditLogAction.overwrite_create,
                             discord.AuditLogAction.overwrite_update,
                             discord.AuditLogAction.overwrite_delete]:
            await handle_permission_changes(entry, log_channel, changes_dict)
            
        elif entry.action in [discord.AuditLogAction.role_create,
                             discord.AuditLogAction.role_update,
                             discord.AuditLogAction.role_delete]:
            await handle_role_changes(entry, log_channel, changes_dict)
            
        elif entry.action in [discord.AuditLogAction.member_role_update]:
            await handle_member_role_changes(entry, log_channel, changes_dict)

        # حفظ في قاعدة البيانات
        save_audit_log(
            guild.id,
            str(entry.action),
            entry.target.id if entry.target else 0,
            str(entry.target) if entry.target else "غير معروف",
            entry.user.id if entry.user else 0,
            str(entry.user) if entry.user else "غير معروف",
            entry.reason or "لا يوجد سبب",
            entry.created_at.isoformat(),
            changes_dict
        )
        
    except Exception as e:
        print(f"خطأ في معالجة audit entry: {e}")

async def handle_channel_changes(entry, log_channel, changes_dict):
    """معالجة تغييرات الرومات"""
    embed = discord.Embed(color=0xff0000 if entry.action == discord.AuditLogAction.channel_delete else 0x00ff00)
    
    if entry.action == discord.AuditLogAction.channel_create:
        embed.title = "🆕 تم إنشاء روم جديد"
        embed.color = 0x00ff00
    elif entry.action == discord.AuditLogAction.channel_update:
        embed.title = "✏️ تم تعديل روم"
        embed.color = 0xffff00
    elif entry.action == discord.AuditLogAction.channel_delete:
        embed.title = "🗑️ تم حذف روم"
        embed.color = 0xff0000

    embed.add_field(name="الروم", value=f"{entry.target.name if entry.target else 'غير معروف'}", inline=True)
    embed.add_field(name="المعدل", value=f"{entry.user.mention if entry.user else 'غير معروف'}", inline=True)
    embed.add_field(name="الوقت", value=f"<t:{int(entry.created_at.timestamp())}:F>", inline=False)
    
    if entry.reason:
        embed.add_field(name="السبب", value=entry.reason, inline=False)
    
    # إضافة التغييرات التفصيلية
    if hasattr(entry, 'before') and hasattr(entry, 'after'):
        changes = []
        for attr in ['name', 'topic', 'position', 'bitrate', 'user_limit']:
            before_val = getattr(entry.before, attr, None)
            after_val = getattr(entry.after, attr, None)
            if before_val != after_val:
                changes.append(f"**{attr}**: `{before_val}` ← `{after_val}`")
                changes_dict[attr] = {'before': str(before_val), 'after': str(after_val)}
        
        if changes:
            embed.add_field(name="التغييرات", value="\n".join(changes), inline=False)

    await log_channel.send(embed=embed)

async def handle_permission_changes(entry, log_channel, changes_dict):
    """معالجة تغييرات الصلاحيات"""
    embed = discord.Embed(title="🔐 تم تعديل صلاحيات", color=0x9932cc)
    
    target_name = "غير معروف"
    if hasattr(entry.target, 'name'):
        target_name = entry.target.name
    elif hasattr(entry, 'extra'):
        target_name = f"الروم: {entry.extra}"
    
    embed.add_field(name="الهدف", value=target_name, inline=True)
    embed.add_field(name="المعدل", value=f"{entry.user.mention if entry.user else 'غير معروف'}", inline=True)
    embed.add_field(name="الوقت", value=f"<t:{int(entry.created_at.timestamp())}:F>", inline=False)
    
    if entry.reason:
        embed.add_field(name="السبب", value=entry.reason, inline=False)
    
    # تفاصيل تغييرات الصلاحيات
    if hasattr(entry, 'before') and hasattr(entry, 'after'):
        perm_changes = []
        
        # مقارنة الصلاحيات
        before_perms = entry.before.permissions if hasattr(entry.before, 'permissions') else None
        after_perms = entry.after.permissions if hasattr(entry.after, 'permissions') else None
        
        if before_perms and after_perms:
            for perm, value in after_perms:
                before_value = getattr(before_perms, perm, None)
                if before_value != value:
                    status = "✅" if value else "❌"
                    perm_changes.append(f"{status} **{perm}**")
                    changes_dict[perm] = {'before': before_value, 'after': value}
        
        if perm_changes:
            embed.add_field(name="تغييرات الصلاحيات", value="\n".join(perm_changes[:10]), inline=False)

    await log_channel.send(embed=embed)

async def handle_role_changes(entry, log_channel, changes_dict):
    """معالجة تغييرات الرتب"""
    embed = discord.Embed(color=0x00ffff)
    
    if entry.action == discord.AuditLogAction.role_create:
        embed.title = "🆕 تم إنشاء رتبة جديدة"
        embed.color = 0x00ff00
    elif entry.action == discord.AuditLogAction.role_update:
        embed.title = "✏️ تم تعديل رتبة"
        embed.color = 0xffff00
    elif entry.action == discord.AuditLogAction.role_delete:
        embed.title = "🗑️ تم حذف رتبة"
        embed.color = 0xff0000

    embed.add_field(name="الرتبة", value=f"{entry.target.name if entry.target else 'غير معروف'}", inline=True)
    embed.add_field(name="المعدل", value=f"{entry.user.mention if entry.user else 'غير معروف'}", inline=True)
    embed.add_field(name="الوقت", value=f"<t:{int(entry.created_at.timestamp())}:F>", inline=False)
    
    if entry.reason:
        embed.add_field(name="السبب", value=entry.reason, inline=False)

    await log_channel.send(embed=embed)

async def handle_member_role_changes(entry, log_channel, changes_dict):
    """معالجة تغييرات رتب الأعضاء"""
    embed = discord.Embed(title="👤 تم تعديل رتب عضو", color=0xffa500)
    
    embed.add_field(name="العضو", value=f"{entry.target.mention if entry.target else 'غير معروف'}", inline=True)
    embed.add_field(name="المعدل", value=f"{entry.user.mention if entry.user else 'غير معروف'}", inline=True)
    embed.add_field(name="الوقت", value=f"<t:{int(entry.created_at.timestamp())}:F>", inline=False)
    
    if entry.reason:
        embed.add_field(name="السبب", value=entry.reason, inline=False)

    await log_channel.send(embed=embed)

@bot.command(name='check_logs')
@commands.has_permissions(administrator=True)
async def check_logs_command(ctx, hours: int = 24):
    """فحص اللوقات لفترة معينة"""
    try:
        guild = ctx.guild
        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        
        embed = discord.Embed(
            title=f"🔍 فحص لوقات آخر {hours} ساعة",
            color=0x00ff00,
            timestamp=datetime.utcnow()
        )
        
        count = 0
        async for entry in guild.audit_logs(limit=200):
            if entry.created_at < datetime.utcnow() - timedelta(hours=hours):
                break
                
            if entry.action in [
                discord.AuditLogAction.channel_create,
                discord.AuditLogAction.channel_update,
                discord.AuditLogAction.channel_delete,
                discord.AuditLogAction.overwrite_create,
                discord.AuditLogAction.overwrite_update,
                discord.AuditLogAction.overwrite_delete,
                discord.AuditLogAction.role_create,
                discord.AuditLogAction.role_update,
                discord.AuditLogAction.role_delete,
                discord.AuditLogAction.member_role_update
            ]:
                await process_audit_entry(entry, log_channel, guild)
                count += 1
        
        embed.add_field(name="النتيجة", value=f"تم العثور على {count} عملية", inline=False)
        await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ خطأ: {e}")

@bot.command(name='status')
async def status_command(ctx):
    """حالة البوت"""
    embed = discord.Embed(title="📊 حالة البوت", color=0x00ff00)
    embed.add_field(name="الحالة", value="🟢 يعمل", inline=True)
    embed.add_field(name="الخادمات", value=f"{len(bot.guilds)}", inline=True)
    embed.add_field(name="المراقبة", value="🔍 نشطة", inline=True)
    await ctx.send(embed=embed)

# تشغيل البوت
if __name__ == "__main__":
    bot.run(BOT_TOKEN)
