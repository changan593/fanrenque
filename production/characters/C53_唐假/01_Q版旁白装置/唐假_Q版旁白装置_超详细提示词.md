# 唐假｜01 Q版旁白装置｜超详细提示词

## 资产元数据

- `state_id`: `C53-TJ-S01-CHIBI`
- 父资产：[`../00_身份母版/`](../00_身份母版/)　**脸、黑印、眼神、服装、无影子全部继承母版，
  本卡只定「二头身化」这一件事**
- 角色卡：`唐假_Q版旁白装置_角色卡.png`（**待生成**）
- 用于：**E01 起全季**，剧本声音栏标 `【现】` 的每一个镜头
- 画面语法：[`../../../s01/_总则与模板.md`](../../../s01/_总则与模板.md) **第五节**（静止世界，一帧不许改）
- 规格源头：[`../../../doc/05_唐假旁白系统.md`](../../../doc/05_唐假旁白系统.md) 4.2

## ★ 二头身是角色设定，不是画风

`doc/05` 4.2 原话。**这一条必须先说清楚，否则会被误读成开了第二种画风：**

- 载体仍然是**国风三维**，用的是**同一套 PBR 材质与全局光照**，渲染锁一个字都不减；
- 变的**只有比例**——二头身；
- 所以他和实景里的人**材质是同一档**：布是真的布，皮肤是真的皮肤。
  ★ **不做塑料玩偶感、不做手办感、不做贴纸感。**

## 继承母版（逐项，不重复写）

脸、五官、头骨、黑发 → 继承 `C01_唐真` 经由母版；
黑印淡一档边缘略虚、**眼睛亮活好奇**、洗旧青灰布袍、麻绳腰带、无配饰、**脚下无影**
→ 全部继承 `00_身份母版`。**本卡不得改动其中任何一条。**

## 本形态的规格

| 项 | 规格 |
| --- | --- |
| 比例 | **二头身**：头与身体各占约二分之一，头略大于身 |
| 脸 | **圆脸**——这是二头身化的结果，是他这个形态的正确长相；**与 `C04` 红儿那种「画胖了」是两回事** |
| 眼 | 大眼睛，**明亮、好奇、带笑意**（母版锁定项④在本形态尤其显眼） |
| 发 | 黑色短发略乱，**几缕翘起** |
| 手脚 | 手小而圆钝，**不做细节化的指节**；赤脚或旧布鞋 |
| 基准姿态 | **歪头**，一只手背在身后，另一只手抬起像要说话 |
| 配色 | 低饱和，**只用青灰、土黄、黑白** |
| ★ 影子 | **脚下没有影子**（母版锁定项③） |

## 空间规则（`doc/05` 4.4 ＋ 总则第五节）

**他不在故事里，画面必须说明这件事：**

- **不与实景同层**：站在画面边缘的"纸面"上、坐画框边沿、从画框下缘探头探肩。
- **不进入景深**：与身后的环境**没有任何透视关系**，不参与遮挡关系。
- **不碰任何东西**、不遮挡剧情主体、**不被任何角色注视**。
- **进出无特效**：某一帧起世界全停，说完转身或摆手即恢复。
- 生图上通常**一镜两张**：底图（冻结的世界）＋**唐假叠加层**，两份提示词分开写。

★ 后期规则行「静止世界：降饱和一档、降亮度半档」是**后期统一处理**，
**不写进提示词**（总则第五节第 5 条）。

## E01 已用姿态（从各幕提示词归纳，本卡为准）

E01 五次现身已经在幕文档里写过五种姿态，**这五种即本形态的基准姿态库**：

| 镜 | 幕 | 姿态 |
| --- | --- | --- |
| 006 | 冷开场 | 从画框正下缘偏左**探出头与双肩**，双手轻搭画框下缘，像趴在窗台边；此刻**收起笑意**、神情安静 |
| 088 | 第三幕 | **坐在画框下缘边沿**，赤脚悬在画框外侧轻轻搭着，笑意收敛，望着画面深处远去的背影 |
| 192 | 第八幕 | 双手背在身后，**贴着画面最下缘自左向右缓缓走** |
| 211 | 第八幕 | 从画面左下角画框边沿**探出头和半个肩膀**，一只小手扒着画框下缘，仰头看着冻结的唐真 |
| — | 第八幕 | **坐在画框边沿、两条小腿垂在框外、双手抱膝**，仰头望着破洞 |

★ **这五种姿态从此是资产**，后续各集优先复用，不要每集新编。
★ 注意 006／088 两处**都收着笑**——`doc/05` 第三节：第一阶段他**语气克制**，
「是在念旁白，不是在演」。**眼睛仍然是亮的**（锁定项④），但笑意可以收。

## 渲染锁

> 逐字取自 [`../../../style_assets/统一角色提示词.md`](../../../style_assets/统一角色提示词.md) 的**短版**，一字不改。

```text
高预算中国院线3D动画电影人物，风格化写实、半写实比例、精致但略带动画化概括的东亚骨相；人物按原文年龄、阶层和生活状态如实塑造，无统一美型模板。身份母版绝对锁定头骨、五官、发际线、耳位、固定不对称与身体比例，所有角度、景别和表情必须是同一人；阶段变化只改指定服装、标志物和表面状态，不换脸。PBR皮肤与克制SSS，稳定眼球高光，成束动画毛发，麻棉丝皮革具有正确织纹、粗糙度和使用痕迹；真实服装受力与人体结构，柔和电影级全局光照。无自动美化、网红脸、蜡像、塑料、真人摄影、二维动漫、美式卡通、游戏宣传感、笔触、噪点和无依据仙侠装饰。
```

> **正式镜头必须再追加克制版约束**：

```text
服从《凡人阙》朴素、克制、反奇观的总体基调。人物首先是生活在具体阶层、地域和处境中的人，不是游戏英雄或仙侠宣传模特。美感来自可信骨相、性格、动作和材质，不来自统一磨皮、夸张身材、华服、首饰、法器、光环和粒子。所有装饰、伤痕、额饰、武器与奇异特征必须有原文或已批准设定依据；没有依据就不添加。布料保留使用痕迹，皮革有折痕，金属有氧化，鞋底与衣摆符合行走环境。修行强弱优先通过眼神、姿态、关系和叙事表现；除原文明写的法术与天地异象外，不主动添加真气流动、发光经脉、悬浮碎片、花瓣、飘带、镜头光晕或宏大能量背景。
```

## 可直接生成提示词

```text
Create 唐假 (Tang Jia) — CHIBI NARRATOR FORM state sheet, derived from his identity master. This is the form used in almost every episode of the series.

PROPORTION IS THE ONLY THING THIS SHEET CHANGES: he is TWO HEADS TALL, head and body each about half his height, the head slightly larger than the body. THE TWO-HEAD PROPORTION IS A CHARACTER SETTING, NOT A CHANGE OF MEDIUM OR STYLE — he is rendered with exactly the same physically based materials, the same cloth, the same skin and the same global illumination as every full-sized character in the production. Do NOT make him look like plastic, like a collectible figurine, like a sticker or like a cartoon insert. His robe is real worn cloth at this scale.

FACE AND IDENTITY, inherited and unchanged: round face at this proportion, LARGE EYES THAT ARE BRIGHT, CURIOUS AND CARRY A TRACE OF AMUSEMENT — his eyes are never allowed to go dead no matter how heavy the scene. A black oval mark centred on the forehead, ONE SHADE LIGHTER THAN THE PROTAGONIST'S with softly diffused edges. Messy short black hair with a few strands sticking up. A washed-out blue-grey cloth robe, collar worn pale, hem frayed, a plain hemp rope tied loosely at the waist, bare feet or old cloth shoes. No headband, no peach branch, no sword, no ornament of any kind. Small blunt hands without detailed knuckles. Desaturated palette of grey-blue, ochre, black and white only.

THE KEY DETAIL, absolute: THERE IS NO SHADOW BENEATH HIS FEET. Not a soft one, not a faint one — none. Where a shadow would fall there is either nothing at all or one shapeless dark ink blot bearing no relation to his silhouette.

BASELINE POSE: head tilted, one hand clasped behind his back, the other raised mid-gesture as if about to speak.

State sheet layout, horizontal 3:2, flat neutral light-grey background, even light, no depth of field. Large baseline figure on the left in the head-tilted speaking pose, full body, feet visible with the empty ground beneath them clearly readable. Centre: front, side and back full-body views at the same scale, plus a macro of the forehead mark and a macro of the bright curious eyes. RIGHT: a POSE LIBRARY of five poses used by the series, all of them staged against the EDGE OF THE PICTURE FRAME rather than inside a scene — peering over the bottom frame edge with both hands resting on it like leaning on a windowsill; sitting on the bottom frame edge with bare feet dangling outside it; walking slowly along the very bottom edge of the frame with both hands behind his back; peering out from the lower-left frame corner with one small hand gripping the frame edge, looking up; and sitting on the frame edge hugging his knees with both shins hanging outside, looking up. In every one of these he sits ON the frame like a figure on the surface of the paper — he never enters the depth of the picture, never overlaps anything in the scene, never touches anything and is never looked at by anyone. BOTTOM: a shadow study — the same figure on neutral ground under even light with ABSOLUTELY NOTHING beneath his feet. No text, no watermark.

高预算中国院线3D动画电影人物，风格化写实、半写实比例、精致但略带动画化概括的东亚骨相；人物按原文年龄、阶层和生活状态如实塑造，无统一美型模板。身份母版绝对锁定头骨、五官、发际线、耳位、固定不对称与身体比例，所有角度、景别和表情必须是同一人；阶段变化只改指定服装、标志物和表面状态，不换脸。PBR皮肤与克制SSS，稳定眼球高光，成束动画毛发，麻棉丝皮革具有正确织纹、粗糙度和使用痕迹；真实服装受力与人体结构，柔和电影级全局光照。无自动美化、网红脸、蜡像、塑料、真人摄影、二维动漫、美式卡通、游戏宣传感、笔触、噪点和无依据仙侠装饰。
```

## 一致性锁定项

- ① **二头身**，且**材质与全片同档**（不是玩偶、不是贴纸）
- ② ★ **眼睛亮、活、好奇**（母版锁定项，本形态最显眼）
- ③ ★ **脚下无影**
- ④ 黑印淡一档、边缘略虚
- ⑤ 圆脸、大眼、短发几缕翘起
- ⑥ 洗旧青灰布袍 ＋ 麻绳腰带，无任何配饰
- ⑦ 低饱和，只用青灰／土黄／黑白
- ⑧ ★ **画框纸面站位**：不进景深、不碰东西、不被注视

## 验收

- [ ] 二头身比例正确，**材质仍是真实布料与皮肤**，不是塑料玩偶
- [ ] 眼睛亮、好奇——即使收起笑意，眼神也不暗
- [ ] **脚下确实没有影子**
- [ ] 黑印比 `C01` 淡一档、边缘晕开
- [ ] 姿态库五种齐全，且**全部贴画框边缘**，没有一种站进景深里
- [ ] 布袍低饱和、领口磨白、袍角毛边
- [ ] 无抹额、无桃枝、无配饰
- [ ] 配色只有青灰／土黄／黑白

## 硬性拒收条件

- ★★ 有影子
- ★★ 眼神暗淡
- ★ 站进画面景深里、与场景发生遮挡或透视关系
- 塑料玩偶感、手办感、贴纸感、卡通描边
- 可爱萌系、大头娃娃、表情包、夸张腮红、星星眼
- 亮色、荧光、高饱和
- 戴抹额或带任何配饰
- 加发光、粒子、半透明幽灵感
