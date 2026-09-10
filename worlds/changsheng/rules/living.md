# 山居生活与物件

调用 `calculate(script="living", arguments_json=...)`。quote 在外层单独传。

操作格式：
- `{"operation":"travel","destination":"courtyard"}`：走到相邻地点，时间来自地图。
- `{"operation":"introduce","name":"细竹签","material":"bamboo","purpose":"拨动门闩"}`：在场景合理存在的普通余料中取出一个小物件。只可在对应地点的 `supplies` 材料内补全；不能增加有经济收益或法术属性的东西。新对象 ID 由脚本返回。
- `{"operation":"manipulate","target":"cloth","property":"wet","value":true,"using":["basin"],"method":"把布浸入水盆"}`：根据对象的 `mutable` 定义改变一种可观察属性。用具必须在手边；有些变化需要标签（如 water、flexible、lever）。`method` 描述玩家具体做法，不能授予不存在的能力。
- `{"operation":"manipulate","target":"door","property":"open","value":true,"using":["bamboo"],"method":"用竹片拨开门闩"}` 仅为格式例子，不要求玩家这样玩。
- `{"operation":"manipulate","target":"door","property":"open","value":true,"using":[],"spell":"dew","method":"让细水线绕过门缝拨闩"}`：可用已掌握的水灵操控，须有近处水源且消耗该功法灵力。不能凭空造水、操控生命或超过普通物件的范围。

普通物件的 `properties` 是已发生事实。只支持声明的可变属性；新的描述不能把不能交易的竹片变成灵石。玩家提出的合理组合应尽量由通用属性与用具标签表达，不能要求预写专用交互 ID。

初始世界小而连通，不设关闭的主线道路。自由生活与可组合物件为本阶段范围；离开已建立地图可以谈论远方，但不能伪称已有完整远行模拟。
