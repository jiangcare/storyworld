"""玩家正文格式契约；系统界面与交流澄清不走 Narrator。"""
import re


def validate_prose(text):
    from .outputs import NarrativeOutput
    text = NarrativeOutput.model_validate({'narrative': text}).narrative.strip()
    if len(text) > 1500:
        raise ValueError('正文过长')
    narration = re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』', '', text, flags=re.S)
    if re.search(r'(?m)^\s*(?:[0-9]+[.、)]\s*|[🎯🎒🧭❤️🏡🌿]|(?:现在可以|当前目标|建议行动|—— 世界记录|已自动保存))', text):
        raise ValueError('正文不得附菜单或状态面板')
    if re.search(r'(?:修为|经验|气血|生命|灵石|熟练度)\s*[：:]?\s*[+\-＋－]\s*\d|[×]\s*\d|\d+\s*/\s*\d+', text):
        raise ValueError('正文不得暴露数值回执')
    if re.search(r'```|(?mi:^\s*(?:\{|EVENT\s*:|NPC\s*:|STATE\s*:|TIME\s*:|PROMPT\s*:|RULE\s*:|(?:count|distance|noise|fear)\s*:))', text):
        raise ValueError('正文不得照抄后台事件字段')
    if re.search(r'你准备怎么办|你选择哪|接下来你|请选择|你可以|是否进入|作为.{0,8}(?:AI|助手)|系统正在|已自动保存', narration):
        raise ValueError('正文不得使用助手式引导')
    if re.search(r'事件触发[：:]|【系统】|(?:恐惧值|好感度|生命值)(?:增加|减少|上升|下降)\s*\d|时间经过\s*\d|Inventory updated', text, re.I):
        raise ValueError('正文不得播报系统事件或属性变化')
    if narration.rstrip().endswith(('?', '？')):
        raise ValueError('旁白不得用问题收尾，场景内 NPC 对话除外')
    if re.search(r'你(?:决定|发誓|承诺|立誓)(?:.{0,12})(?:拼死|保护|效忠|加入|背叛|杀死|献出|爱上)', narration):
        raise ValueError('叙述不能擅自决定角色重大立场')
    return text
