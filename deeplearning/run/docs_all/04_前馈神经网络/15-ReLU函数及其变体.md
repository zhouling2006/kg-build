---
title: ReLU函数及其变体
id: d1eec128-29c0-421a-baa8-f1072fdd4cbf
type: topic
aliases:
  - 修正线性单元
  - Rectified Linear Unit
tags:
  - 深度学习
  - 前馈神经网络
  - 激活函数
description: 介绍ReLU函数的定义、优缺点及其多种变体（Leaky ReLU、PReLU、ELU、Softplus）的数学形式与特性。
terms:
  - id: 1
    name: ReLU函数
    aliases:
      - 修正线性单元
      - Rectifier函数
    definition: 定义为$f(x)=\max(0,x)$的激活函数，具有左饱和、计算高效及稀疏激活性，能有效缓解梯度消失。
  - id: 2
    name: 死亡ReLU问题
    aliases:
      - Dying ReLU Problem
    definition: 训练时若ReLU神经元在负半轴被激活且参数更新不当，导致其梯度持续为0而无法恢复激活的现象。
  - id: 3
    name: 带泄露的ReLU
    aliases:
      - Leaky ReLU
    definition: ReLU的变体，在输入小于0时保留一个很小的固定梯度，以降低神经元死亡的风险。
  - id: 4
    name: 带参数的ReLU
    aliases:
      - PReLU
      - Parametric ReLU
    definition: ReLU的变体，将负半轴的斜率设为可学习参数，允许不同神经元具有不同的负半轴梯度。
  - id: 5
    name: ELU函数
    aliases:
      - 指数线性单元
      - Exponential Linear Unit
    definition: 在负半轴使用指数函数的激活函数，具有近似零中心化的特性。
  - id: 6
    name: Softplus函数
    definition: ReLU的平滑版本，定义为$\log(1+\exp(x))$，其导数为Logistic函数，但不具备稀疏激活性。
source: 第4章 前馈神经网络/ReLU函数及其变体.md
---

# ReLU函数及其变体

ReLU函数及其变体是一类在深度神经网络中广泛使用的逐元素激活函数，旨在解决传统Sigmoid型函数存在的梯度消失与非零中心化问题，同时提供计算高效性与稀疏激活性。

## ReLU函数的定义与特性

<!-- @anchor-begin id="relu-def" terms="1" note="ReLU函数的正式定义" -->
**ReLU函数**（Rectified Linear Unit，修正线性单元），也叫Rectifier函数，是深度神经网络中常用的激活函数。它实际上是一个斜坡（ramp）函数，其数学定义为：
$$
f(x) = \begin{cases} x & x \geq 0 \\ 0 & x < 0 \end{cases} = \max(0, x)
$$
<!-- @anchor-end -->

<!-- @anchor-begin id="relu-pros-cons" terms="1|2" note="ReLU的优缺点及死亡ReLU问题" -->
**优点**：
1. **计算高效**：采用ReLU的神经元只需要进行加、乘和比较的操作。
2. **生物学合理性**：具有单侧抑制、宽兴奋边界的特性。
3. **稀疏激活性**：在生物神经网络中，同时处于兴奋状态的神经元非常稀疏。Sigmoid型激活函数会导致非稀疏的神经网络，而ReLU具有很好的稀疏性，大约50%的神经元会处于激活状态。
4. **缓解梯度消失**：相比于Sigmoid型函数的两端饱和，ReLU函数为左饱和函数，且在 $x > 0$ 时导数为1，在一定程度上缓解了神经网络的梯度消失问题，加速梯度下降的收敛速度。

**缺点**：
1. **非零中心化**：ReLU函数的输出恒大于等于0，是非零中心化的。这会给后一层的神经网络引入偏置偏移（Bias Shift），影响梯度下降的效率。
2. **死亡ReLU问题**：ReLU神经元在训练时比较容易“死亡”。如果参数在一次不恰当的更新后，某个ReLU神经元在所有的训练数据上都不能被激活（即输入始终落在 $x < 0$ 的区域），那么该神经元自身参数的梯度会持续为0。在后续训练过程中，该神经元很难再恢复激活，这种现象称为**死亡ReLU问题**（Dying ReLU Problem）。
<!-- @anchor-end -->

## ReLU的常见变体

为了缓解ReLU的非零中心化与死亡ReLU问题，研究者提出了多种变体。

### 带泄露的ReLU（Leaky ReLU）

<!-- @anchor-begin id="leaky-relu" terms="3" note="Leaky ReLU定义" -->
**带泄露的ReLU**（Leaky ReLU）在输入 $x < 0$ 时，保持一个很小的梯度 $\gamma$。这样当神经元非激活时也能有一个非零的梯度可以更新参数，降低长期不能被激活的风险。其定义为：
$$
\text{Leaky ReLU}(x) = \begin{cases} x & \text{if } x > 0 \\ \gamma x & \text{if } x \leq 0 \end{cases} = \max(0, x) + \gamma \min(0, x)
$$
其中 $\gamma$ 是一个很小的常数（如0.01）。当 $\gamma < 1$ 时，也可以等价写为 $\text{Leaky ReLU}(x) = \max(x, \gamma x)$。
<!-- @anchor-end -->

### 带参数的ReLU（PReLU）

<!-- @anchor-begin id="prelu" terms="4" note="PReLU定义" -->
**带参数的ReLU**（Parametric ReLU，PReLU）将负半轴的斜率设为一个可学习的参数，允许不同神经元具有不同的参数。对于第 $i$ 个神经元，其定义为：
$$
\text{PReLU}_i(x) = \begin{cases} x & \text{if } x > 0 \\ \gamma_i x & \text{if } x \leq 0 \end{cases} = \max(0, x) + \gamma_i \min(0, x)
$$
其中 $\gamma_i$ 为 $x \leq 0$ 时函数的斜率。PReLU是非饱和函数，如果 $\gamma_i = 0$ 则退化为ReLU，如果 $\gamma_i$ 为很小的常数则等同于Leaky ReLU。
<!-- @anchor-end -->

### ELU函数

<!-- @anchor-begin id="elu" terms="5" note="ELU定义" -->
**ELU函数**（Exponential Linear Unit，指数线性单元）是一个近似的零中心化的非线性函数。它在负半轴使用指数函数，定义为：
$$
\text{ELU}(x) = \begin{cases} x & \text{if } x > 0 \\ \gamma(\exp(x) - 1) & \text{if } x \leq 0 \end{cases} = \max(0, x) + \min(0, \gamma(\exp(x) - 1))
$$
其中 $\gamma \geq 0$ 是一个超参数，决定 $x \leq 0$ 时的饱和曲线，并调整输出均值在0附近，从而缓解非零中心化带来的偏置偏移问题。
<!-- @anchor-end -->

### Softplus函数

<!-- @anchor-begin id="softplus" terms="6" note="Softplus定义" -->
**Softplus函数**可以看作ReLU函数的平滑版本，其定义为：
$$
\text{Softplus}(x) = \log(1 + \exp(x))
$$
Softplus函数的导数刚好是Logistic函数。虽然它也具有单侧抑制、宽兴奋边界的特性，但由于其在整个定义域上均不为0，因此**没有稀疏激活性**。
<!-- @anchor-end -->

![](images/cd541d1309d60344aa250858bf621c9075dc9de3c56e58283659168e917dec07.jpg)（描述：示意图，展示了ReLU、Leaky ReLU、ELU和Softplus四种激活函数的曲线对比）  
图 4.8 ReLU、Leaky ReLU、ELU 以及 Softplus 函数

## 相关知识点

[[Sigmoid型函数]]{type=topic, label=Sigmoid型函数, render=link}
[[Swish与GELU函数]]{type=topic, label=Swish与GELU函数, render=link}
[[Maxout单元]]{type=topic, label=Maxout单元, render=link}