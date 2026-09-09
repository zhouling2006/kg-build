---
title: Sigmoid型函数
id: c0e0e1c8-688a-48c0-9d99-bece247ecaad
type: topic
aliases:
  - S型函数
  - Sigmoid函数
tags:
  - 深度学习
  - 前馈神经网络
  - 激活函数
description: 介绍两端饱和的Sigmoid型函数（Logistic与Tanh）的数学定义、零中心化特性及其在梯度消失与偏置偏移中的影响。
terms:
  - id: 1
    name: Sigmoid型函数
    aliases:
      - S型函数
    definition: 一类两端饱和的S型曲线函数，主要包括Logistic函数和Tanh函数。
  - id: 2
    name: 饱和
    definition: 函数在自变量趋于无穷时导数趋于0的性质，分为左饱和、右饱和和两端饱和。
  - id: 3
    name: Logistic函数
    aliases:
      - Sigmoid函数
    definition: 一种将实数域输入挤压至(0,1)区间的Sigmoid型函数，常用于二分类输出层。
  - id: 4
    name: Tanh函数
    aliases:
      - 双曲正切函数
    definition: 一种值域为(-1,1)的Sigmoid型函数，具有零中心化特性。
  - id: 5
    name: 零中心化
    aliases:
      - Zero-Centered
    definition: 激活函数输出的均值为0的特性，有助于减少后一层神经元的偏置偏移。
  - id: 6
    name: 偏置偏移
    aliases:
      - Bias Shift
    definition: 非零中心化输出导致后一层神经元输入发生偏移，从而减慢梯度下降收敛速度的现象。
source: 第4章 前馈神经网络/Sigmoid型函数.md
---

# Sigmoid型函数

Sigmoid型函数是一类两端饱和的S型曲线函数，主要包括Logistic函数和Tanh函数，它们通过非线性映射将实数域输入挤压至有限区间，是早期神经网络中最常用的激活函数。

## 定义与饱和特性

<!-- @anchor-begin id="sigmoid-saturation" terms="1|2" note="定义Sigmoid型函数及饱和概念" -->
**Sigmoid型函数**是指一类S型曲线函数，其核心特征为**两端饱和**。

对于函数 $f(x)$，若 $x \to -\infty$ 时其导数 $f'(x) \to 0$，则称其为左饱和；若 $x \to +\infty$ 时其导数 $f'(x) \to 0$，则称其为右饱和。当同时满足左、右饱和时，就称为两端饱和。常用的Sigmoid型函数有Logistic函数和Tanh函数。
<!-- @anchor-end -->

## Logistic函数

<!-- @anchor-begin id="logistic-function" terms="3" note="Logistic函数的定义与性质" -->
**Logistic函数**定义为：
$$
\sigma(x) = \frac{1}{1 + \exp(-x)}
$$
其导数为：
$$
\sigma'(x) = \sigma(x)(1 - \sigma(x)) \in [0, 0.25]
$$
<!-- @anchor-end -->

Logistic函数可以看成是一个“挤压”函数，把一个实数域的输入“挤压”到 $(0,1)$ 区间。当输入值在0附近时，函数近似为线性；当输入值靠近两端时，对输入进行抑制。输入越小越接近于0，输入越大越接近于1。

装备了Logistic激活函数的神经元具有以下特点：
1. 在二分类任务的输出层中，其输出可以解释为类别 $y=1$ 的条件概率，使神经网络更好地与统计学习模型结合。
2. 可看作一个软性门（Soft Gate），用来控制其他神经元输出信息的数量。

## Tanh函数

<!-- @anchor-begin id="tanh-function" terms="4" note="Tanh函数的定义与性质" -->
**Tanh函数**（双曲正切函数）也是一种Sigmoid型函数，其定义为：
$$
\tanh(x) = \frac{\exp(x) - \exp(-x)}{\exp(x) + \exp(-x)}
$$
其导数为：
$$
\tanh'(x) = 1 - (\tanh(x))^2 \in [0, 1]
$$
<!-- @anchor-end -->

Tanh函数可以看作放大并平移的Logistic函数，其值域是 $(-1, 1)$，两者满足如下数学关系：
$$
\tanh(x) = 2\sigma(2x) - 1
$$

![](../images/9de52c1cf3b4fa8e47576a89af9f7acb8d6b0404d2e702b42d91d660a8f082a1.jpg)  
(a) Logistic 函数的导数

![](../images/09e892f8973275198dfd9d3325f49d6148b502050894672013b97e2cc8aafd8a.jpg)  
(b) Tanh 函数的导数  
图 4.6 Sigmoid型函数的导数

![](../images/954fbd7f481642bfe889dd4344b411eb70dc61f54735d18b9519f4de3d32b6da.jpg)（描述：该图是Logistic函数和Tanh函数的曲线示意图，展示了两者在不同输入值下的输出变化趋势，其中Logistic函数为实线，Tanh函数为虚线）  
图 4.7 Logistic 函数和 Tanh 函数

## 零中心化与偏置偏移

<!-- @anchor-begin id="zero-centered-bias-shift" terms="5|6" note="对比零中心化与偏置偏移" -->
Tanh函数的输出是**零中心化**（Zero-Centered）的，而Logistic函数的输出恒大于0。

非零中心化的输出会使得其后一层的神经元的输入发生**偏置偏移**（Bias Shift），并进一步使得梯度下降的收敛速度变慢。因此，在隐藏层中，Tanh函数通常比Logistic函数更受青睐，因为它能减少偏置偏移，加快收敛。
<!-- @anchor-end -->

## 优化缺陷：梯度消失

尽管Sigmoid型函数具有平滑、可导等良好的数学性质，但它们在深层网络训练中面临严重的优化缺陷。

由于Sigmoid型函数的两端饱和性，在饱和区其导数接近于0（Logistic函数导数最大仅为0.25）。在反向传播过程中，误差项在每一层都要乘以该层激活函数的导数。当网络层数很深时，这些小于1的导数连乘会导致梯度不断衰减甚至消失，使得整个网络很难训练，这就是所谓的**梯度消失问题**。

## 相关知识点

- [[人工神经元模型]]{type=topic, label=人工神经元模型, render=link}
- [[梯度消失与梯度爆炸]]{type=topic, label=梯度消失与梯度爆炸, render=link}
- [[ReLU函数及其变体]]{type=topic, label=ReLU函数及其变体, render=link}
- [[Swish与GELU函数]]{type=topic, label=Swish与GELU函数, render=link}