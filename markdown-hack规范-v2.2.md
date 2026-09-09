# markdown-hack 规范 v2.3

## 1. 目标与定位

本规范用于指导生成可扩展、可索引、可导航的结构化 Markdown 知识文档。

**v2.3 新增**：二层节点架构（主干 concept + 附属 proof/example），支持轻量知识骨架 + 可插拔补充材料。

重点支持：

- 文档级 `doc-metadata`（Frontmatter）
- 文档内术语自描述（`terms` 列表）
- 多别名、多标签
- 页面引用、块引用、锚点引用三级跳转
- 引用附加结构化信息（题目、知识点、定理、锚点位置）
- 段落级术语锚定（支持同一段落被多个术语同时锚定）
- **文档 id 采用 UUID v4 格式（如 `550e8400-e29b-41d4-a716-446655440000`），全局唯一；文档引用使用中文 title，术语与锚点 id 仍采用英文小写+连字符，避免编码问题**
- **二层架构：主干节点（concept）存核心知识，附属节点（proof/example）存证明和例题，独立可插拔**

---

## 2. doc-metadata（Frontmatter）规范

每个文档顶部应使用 YAML Frontmatter 表达 `doc-metadata`。

### 2.1 基础字段

```yaml
---
title: 极限的严格定义
id: 550e8400-e29b-41d4-a716-446655440000
type: topic
node_type: concept
aliases:
  - 函数极限定义
  - ε-δ 语言定义
tags:
  - 高等数学
  - 极限
  - 基础概念
description: 使用 ε-δ 语言严格刻画函数在某一点的极限行为。
---
```

#### 字段定义

| 字段 | 必填 | 说明 |
|------|------|------|
| `title` | 是 | 文档主标题，单个字符串 |
| `id` | 是 | 文档唯一标识符，UUID v4 格式（如 `550e8400-e29b-41d4-a716-446655440000`），全局唯一 |
| `type` | 是 | 文档类型：`topic`（概念性知识点）、`theorem`（定理/性质）、`question`（习题/题目）、`example`（例题/场景）、`index`（索引/目录节点） |
| `node_type` | 是（v2.3 新增） | 二层架构节点类型：`concept`（主干概念，仅含定义/定理/公式/性质）、`proof`（证明节点）、`example`（例题节点） |
| `parent_concept` | proof/example 必填 | 附属节点挂载的父概念 title；concept 节点不填或填 null |
| `aliases` | 否 | 别名列表，字符串数组；省略时可不填 |
| `tags` | 否 | 标签列表，字符串数组；省略时可不填 |
| `description` | 否 | 一句话描述，用于索引展示 |

#### 可选拓展字段

| 字段 | 说明 |
|------|------|
| `date` | 创建日期 |
| `updated` | 更新日期 |
| `version` | 文档版本 |

### 2.2 术语列表（terms）

用于在文档内自描述本知识点涉及的所有术语名词，供索引系统提取并入全局术语索引。

```yaml
terms:
  - id: 1
    name: 极限
    aliases:
      - 函数极限
    definition: 当自变量趋近于某一点时，函数值趋近于一个确定常数的性质。
  - id: 2
    name: ε-δ 语言
    definition: 用任意小的正数 ε 及其对应的 δ 来严格描述极限过程的数学语言。
```

#### 字段定义

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | 是 | 术语唯一标识（文档内唯一）。**数字编号，从 1 开始**，如 `1`、`2` |
| `name` | 是 | 术语中文名称，用于显示 |
| `aliases` | 否 | 术语别名列表；无别名时省略 |
| `definition` | 是 | 一句话释义，用于索引和术语列表展示 |

**约定：**
- 仅当术语在本文档中有**实际内容被讲解**，或它本身就是本知识点的组成部分时，才列入列表。
- `id` 在文档内必须唯一，后续锚定标记通过此 `id` 关联。
- 如本文档无术语，`terms` 应返回空列表 `terms: []`。

### 2.3 Frontmatter 完整示例

```yaml
---
title: 极限的严格定义
id: 550e8400-e29b-41d4-a716-446655440000
type: topic
aliases:
  - 函数极限定义
tags:
  - 高等数学
  - 极限
description: 使用 ε-δ 语言严格刻画函数在某一点的极限行为。
terms:
  - id: 1
    name: 极限
    aliases:
      - 函数极限
    definition: 当自变量趋近于某一点时，函数值趋近于一个确定常数的性质。
  - id: 2
    name: ε-δ 语言
    definition: 用任意小的正数 ε 及其对应的 δ 来严格描述极限过程的数学语言。
---
```

---

## 3. 锚定标记规范

锚定标记用于**将术语与内容中出现的具体段落/块进行绑定**，实现「术语 → 原文位置」的精确跳转，同时支持**同一段落被多个术语同时锚定**。

锚定标记采用 **HTML 注释语法**，对任何标准 Markdown 渲染器完全不可见、不干扰阅读，同时可被解析器精确提取。

### 3.1 包裹式锚定（推荐）

用于锚定一个内容块（可包含多段、列表、公式、代码等）。

```md
<!-- @anchor-begin id="limit-strict-def" terms="limit|epsilon-delta" note="同时定义极限与ε-δ语言" -->
设函数 $f(x)$ 在点 $x_0$ 的某去心邻域内有定义。

若存在常数 $L$，使得对于任意给定的正数 $\varepsilon$（无论它多么小），总存在正数 $\delta$，当 $0<|x-x_0|<\delta$ 时，有 $|f(x)-L|<\varepsilon$，则称 $L$ 为函数 $f(x)$ 当 $x \to x_0$ 时的**极限**。
<!-- @anchor-end -->
```

#### 语法说明

| 属性         | 必填  | 说明                                              |                          |
| ---------- | --- | ----------------------------------------------- | ------------------------ |
| `id`       | 是   | 锚点唯一标识（文档内唯一）。**英文小写+连字符**，如`limit-strict-def` |                          |
| `terms` | 是   | 关联的术语 `id` 列表，多个术语用 `\|` 分隔。**同一段落可被多个术语同时锚定。** |
| `note`     | 否   | 锚点备注，说明此处为何锚定这些术语                               |                          |

**约定：**
- `<!-- @anchor-begin ... -->` 与 `<!-- @anchor-end -->` 必须成对出现。
- 被包裹的内容在渲染时**保持原样**，不添加任何样式或 DOM 包裹。
- 解析器提取时，将 `id` 映射到该内容块在文档中的位置（行号/块索引）。
- 术语 `id` 来自本文档 Frontmatter 中的 `terms` 列表。

### 3.2 单点锚定（行内/不可包裹场景）

用于表格内、标题行、列表项等不方便使用包裹式的场景，仅标记一个精确位置。

```md
<!-- @anchor-point id="limit-continuity-link" terms="limit" /-->
```

此标记不占内容空间，仅作为位置指针。解析器将其定位到紧随其后的第一个有效内容元素（段落、列表项、表格单元格等）。

### 3.3 ID 命名规范

**原则：英文小写+连字符，直观可读，避免编码问题。**

#### 命名规则

1. **全部使用英文小写字母**
2. **单词之间用连字符 `-` 连接**
3. **允许使用数字**
4. **禁止使用中文、特殊符号、空格**

#### 命名示例

| 内容描述 | 推荐 ID | 不推荐 |
|----------|---------|--------|
| 极限的严格定义 | `limit-strict-def` | `极限的严格定义`, `jixian-de-yange-dingyi` |
| 用 ε-δ 语言证明 | `epsilon-delta-proof` | `用εδ语言证明`, `proof-eps-delta` |
| 例题 1 | `example-1` | `例题1`, `li-ti-1` |
| 图 3-2 | `fig-3-2` | `图3-2`, `tu-3-2` |
| 极限与连续性的衔接 | `limit-continuity-link` | `极限与连续性的衔接`, `limit-continuity`（太泛） |
| x 趋于 0 时的极限 | `limit-as-x-to-0` | `x趋于0时的极限`, `x-tends-to-0-limit` |

### 3.4 多术语锚定示例

以下示例展示**同一段落被两个术语同时锚定**：

```md
<!-- @anchor-begin id="limit-continuity-link" terms="limit|continuity" note="极限与连续性的衔接点" -->
由此可见，函数在某点连续的本质，就是该点的函数值等于极限值。因此，**连续性**可以看作是极限的一种特殊应用。
<!-- @anchor-end -->
```

解析器应识别：该段落同时属于 `limit` 和 `continuity` 两个术语的内容锚定。

---

## 4. 基础引用语法

兼容并扩展 Logseq 风格引用。

### 4.1 页面引用

```md
[[页面名]]
```

跳转到目标文档。若目标文档存在同名锚点，可结合扩展字段精确跳转。

### 4.2 块引用（本文档内）

```md
[[#块名]]
```

引用本文档内已定义的语义块（见第 6 节）。块名对应语义块定义中的 `#id`。

### 4.3 跨文档引用（带作用域）

```md
[[文档title#块名]]
[[文档title#锚点名]]
```

引用其他知识点文档内的语义块或锚点。

| 写法 | 含义 |
|------|------|
| `[[极限的严格定义#proof-limit-def]]` | 引用知识点「极限的严格定义」中名为 `proof-limit-def` 的语义块 |
| `[[极限的严格定义#limit-strict-def]]` | 引用知识点「极限的严格定义」中名为 `limit-strict-def` 的锚点 |
| `[[#proof-limit-def]]` | 引用本文档中名为 `proof-limit-def` 的语义块（可省略文档 title） |

**约定：**
- `#` 前为文档 `title`，为空表示本文档。
- `#` 后为局部块名或锚点名，文档内唯一。
- 解析器实现：按 `#` 分割，前半部分查找目标文档，后半部分查找文档内的块/锚点。

---

## 5. 引用扩展 Hack

在基础引用主体后增加 `{...}` 扩展信息区，携带附加结构化信息。

统一写法：

```md
[[引用目标]]{key=value, key=value}
```

### 5.1 扩展字段总览

| 字段 | 必填 | 说明 |
|------|------|------|
| `type` | 是 | 引用类型：`topic`（知识点文档）、`concept`（名词术语）、`theorem`（定理/性质）、`question`（习题）、`anchor`（锚点）、`proof`（证明）、`example`（例题）等 |
| `label` | 否 | 展示名称（中文），用于 `render=inline` 时的显示文本 |
| `render` | 否 | 渲染方式：`link`（默认）、`card`、`embed`、`inline` |
| `anchor` | 否 | 目标文档内的锚点 ID，用于精确位置跳转。若引用主体已含 `#锚点名`，此字段可省略 |
| `term_id` | 否 | 目标术语在 Frontmatter `terms` 中的 `id`，用于术语级精确关联 |
| `id` | 否 | 外部编号或题号 |
| `tags` | 否 | 引用级标签，多个值用 `\|` 分隔 |
| `note` | 否 | 补充说明 |

### 5.2 渲染方式（render）

| 值 | 说明 | 适用场景 |
|----|------|---------|
| `link` | 默认，渲染为超链接，显示完整引用目标 | 一般引用 |
| `card` | 渲染为卡片，显示标题+摘要 | 定理、证明、例题 |
| `inline` | 只显示 `label` 的值，带链接，不额外渲染 | **原文中的概念词加链接** |

### 5.3 各类型引用扩展字段

#### 知识点文档引用（`type=topic`）

引用一个独立的知识点文档整体，用于"相关知识点"块等场景。

```md
[[极限的严格定义]]{type=topic, label=极限的严格定义, render=link}
```

| 字段 | 说明 |
|------|------|
| `label` | 显示名称 |

#### 名词术语引用（`type=concept`）

引用文档内 Frontmatter `terms` 中定义的术语，精确到术语 `id` 级别。用于行内概念词加链接等场景。

```md
[[极限的严格定义#1]]{type=concept, label=极限, render=inline}
```

| 字段 | 说明 |
|------|------|
| `term_id` | 目标术语 `id`，优先于页面名匹配。解析器先定位文档，再在文档内查找该术语 |
| `label` | 显示名称。**`render=inline` 时必填**，决定原文中显示什么文字 |
| `render=inline` | 原文中的概念词加链接，显示 `label`，背后链接到引用目标 |

#### 定理/性质引用（`type=theorem`）

```md
[[介值定理#ivt]]{type=theorem, label=介值定理, render=card}
```

| 字段 | 说明 |
|------|------|
| `anchor` | 跳转到定理声明的具体锚点或语义块 |
| `corollary` | 若引用推论，填写推论序号或名称 |

#### 习题/题目引用（`type=question`）

用于引用课后习题、考试题等需要独立解答的题目。

```md
[[同济高数习题-极限-例1]]{type=question, id=例1, label=用定义证明极限, render=card, source=同济高数, answer=略, tags=高数|极限|证明题}
```

| 字段 | 说明 |
|------|------|
| `id` | 题号 |
| `label` | 题目简称 |
| `source` | 来源（如教材、试卷） |
| `answer` | 答案或结论 |
| `tags` | 题目标签 |

#### 例题/场景引用（`type=example`）

用于引用教材中的例题、教学示例或应用场景说明。

```md
[[极限的严格定义#example-1]]{type=example, id=例1, label=用定义证明极限, render=card, source=同济高数, tags=高数|极限|证明题}
```

| 字段 | 说明 |
|------|------|
| `id` | 例号 |
| `label` | 示例简称 |
| `source` | 来源 |
| `tags` | 示例标签 |

#### 锚点引用（`type=anchor`）

用于直接引用文档内的某个锚定内容块。

```md
[[极限的严格定义#limit-strict-def]]{type=anchor, label=ε-δ原文段落, render=embed}
[[#limit-strict-def]]{type=anchor, render=card, note=极限定义核心段落}
```

#### 语义块引用（`type=proof`、`type=example` 等）

```md
[[极限的严格定义#proof-limit-def]]{type=proof, label=极限定义证明, render=card}
[[#example-1]]{type=example, label=用定义证明极限}
```

### 5.4 行内引用（render=inline）详解

**用途**：在已有原文中，给某个概念词加上链接，不改变原文阅读体验。

**原文：**
> 理解极限的概念是掌握连续性的基础。

**加工后：**
```md
理解[[极限的严格定义#1]]{type=concept, label=极限, render=inline}的概念是掌握[[连续性#1]]{type=concept, label=连续性, render=inline}的基础。
```

**渲染结果：**
> 理解**极限**的概念是掌握**连续性**的基础。

「极限」和「连续性」都是可点击链接，分别跳到对应位置。阅读体验完全自然。

**约定：**
- `render=inline` 时，`label` 为必填，决定显示文字。
- 引用主体 `[[title#term_id]]` 中的 `term_id` 对应目标文档 Frontmatter `terms` 列表中该术语的 `id`。
- 若目标术语有多个锚点，优先跳转到第一个锚点位置。

---

## 6. 语义块定义语法

语义块用于定义文档内具有明确边界和类型的「内容型大块」，如定理、证明、例题、推导等。

**语义块仅承担「内容结构化」职责，锚定统一由第 3 节的锚定标记负责。**

### 6.1 内容型语义块（Pandoc fenced div 风格）

```md
::: {#ivt .theorem label="闭区间上连续函数的介值定理" type=theorem}
若函数 $f(x)$ 在闭区间 $[a,b]$ 上连续，且 $f(a) \cdot f(b) < 0$，则至少存在一点 $\xi \in (a,b)$，使得 $f(\xi)=0$。
:::
```

```md
::: {#proof-limit-def .proof label="极限定义证明过程" type=proof}
取任意 $\varepsilon > 0$，构造 $\delta = \min\{1, \frac{\varepsilon}{3}\}$，则当 $0<|x-x_0|<\delta$ 时……
:::
```

#### 语法说明

| 属性 | 说明 |
|------|------|
| `#id` | 块唯一标识（文档内唯一）。**英文小写+连字符**，如 `ivt`、`proof-limit-def` |
| `.class` | 块 CSS 类，可多个（如 `.theorem .important`） |
| `label="..."` | 块展示标签（中文） |
| `type=...` | 块内容类型：`theorem`、`proof`、`definition`、`example`、`remark`、`corollary` 等 |

**与锚定标记配合使用：**

若定理块本身也需要被术语锚定，在语义块**外部**包裹锚定标记：

```md
<!-- @anchor-begin id="ivt" terms="continuity" -->
::: {#ivt .theorem label="介值定理" type=theorem}
若函数 $f(x)$ 在闭区间 $[a,b]$ 上连续……
:::
<!-- @anchor-end -->
```

---

## 7. 图像资源规范

若文档有图像需求，图片文件应放在该 Markdown 同级目录的 `images` 子目录。

路径写法统一为相对路径：

```md
![说明文本](./images/xxx.png)
```

约定：

- 不使用绝对路径
- 不跨目录回溯（如 `../`）引用图片
- 文件名建议使用英文、小写、连字符或数字

---

## 8. 完整示例

以下是一份符合 v2.2 规范的完整知识文档示例：

```md
---
title: 极限的严格定义
id: 550e8400-e29b-41d4-a716-446655440000
type: topic
aliases:
  - 函数极限定义
  - ε-δ 语言定义
tags:
  - 高等数学
  - 极限
  - 基础概念
description: 使用 ε-δ 语言严格刻画函数在某一点的极限行为。
terms:
  - id: 1
    name: 极限
    aliases:
      - 函数极限
    definition: 当自变量趋近于某一点时，函数值趋近于一个确定常数的性质。
  - id: 2
    name: ε-δ 语言
    definition: 用任意小的正数 ε 及其对应的 δ 来严格描述极限过程的数学语言。
---

## 直观理解

<!-- @anchor-begin id="intuitive-limit" terms="1" note="极限的直观描述" -->
在中学数学中，我们接触过数列的极限。对于函数而言，极限描述的是：当自变量 $x$ 无限接近某一点 $x_0$ 时，函数值 $f(x)$ 的变化趋势。
<!-- @anchor-end -->

## 严格定义（ε-δ 语言）

<!-- @anchor-begin id="limit-strict-def" terms="1|2" note="同时锚定极限与ε-δ语言" -->
设函数 $f(x)$ 在点 $x_0$ 的某去心邻域内有定义。若存在常数 $L$，使得对于**任意**给定的正数 $\varepsilon$（无论它多么小），总存在正数 $\delta$，当 $0<|x-x_0|<\delta$ 时，有 $|f(x)-L|<\varepsilon$，则称 $L$ 为函数 $f(x)$ 当 $x \to x_0$ 时的**极限**，记作：

$$\lim_{x \to x_0} f(x) = L$$
<!-- @anchor-end -->

## 极限与连续性的衔接

<!-- @anchor-begin id="limit-continuity-link" terms="1" note="极限是连续性的基础" -->
由此可见，函数在某点连续的本质，就是该点的函数值等于极限值。因此，[[连续性#1]]{type=concept, label=连续性, render=inline}可以看作是[[极限的严格定义#1]]{type=concept, label=极限, render=inline}的一种特殊应用。
<!-- @anchor-end -->

## 证明示例

参考 [[极限的严格定义#proof-limit-def]]{type=proof, label=极限定义证明, render=card}。

::: {#proof-limit-def .proof label="标准证明模板" type=proof}
取任意 $\varepsilon > 0$，构造 $\delta = \min\{1, \frac{\varepsilon}{3}\}$，则当 $0<|x-x_0|<\delta$ 时……
（此处省略具体推导）
:::

## 相关知识点

- 前置基础：[[实数完备性]]{type=topic, label=实数理论, render=link}
- 直接应用：[[连续性]]{type=topic, label=连续性, render=link}
- 对比区分：[[数列极限]]{type=topic, label=数列极限, render=link, tags=极限|对比}
- 证明细节：[[#proof-limit-def]]{type=proof, label=极限证明模板, render=card}

## 例题

[[同济高数例-极限-例1]]{type=example, id=例1, label=用定义证明极限, render=card, source=同济高数, tags=高数|极限|证明题}
```

---

## 9. 解析器推荐解析原则

1. **扫描 Frontmatter**：提取 `terms` 构建文档级术语索引。
2. **扫描锚定标记**：按顺序提取所有 `<!-- @anchor-begin ... -->` 到 `<!-- @anchor-end -->` 之间的内容块，建立 `锚点ID → [术语ID列表] → 内容块位置` 的映射。
3. **扫描语义块**：提取 `::: {#id ...}` 到 `:::` 之间的内容，建立块索引，块 ID 为文档内局部名。
4. **扫描引用**：
   - 若引用后紧跟 `{...}`，按增强引用解析；
   - `type` 用于区分语义类型；
   - 引用主体含 `#` 时，按 `title#局部名` 解析；不含 `#` 时视为页面级引用；
   - `anchor` 字段存在时，作为 `#` 后的补充或覆盖；
   - `term_id` 存在时，在目标文档的 `terms` 列表中查找该术语，优先跳转到其锚定位置；
   - `render=inline` 时，显示 `label` 的值，并给该文字加上指向引用目标的链接。
5. **构建术语-内容反向索引**：根据锚定标记，为每个术语 `id` 收集其被锚定的所有 `锚点ID` 及对应内容块。
6. **未识别字段**：在扩展信息区中保留未识别字段，供后续扩展。
7. **`tags` 分隔**：引用扩展中的 `tags` 使用 `\|` 分隔多个值，避免与字段分隔逗号冲突。

---



