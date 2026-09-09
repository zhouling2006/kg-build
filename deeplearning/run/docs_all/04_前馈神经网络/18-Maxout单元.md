---
title: Maxout单元
id: 194c23e9-e682-44d2-ab97-d637033bb2c9
type: topic
aliases:
  - Maxout Network
tags:
  - 深度学习
  - 前馈神经网络
  - 激活函数
  - 非线性结构
description: 介绍Maxout单元的定义、计算机制及其作为复杂非线性模块的特性。
terms:
  - id: 1
    name: Maxout单元
    definition: 一种建立在向量输入之上的复杂非线性模块，包含多组可学习参数，通过计算多个净输入并取最大值来实现非线性映射。
  - id: 2
    name: 分段线性近似
    definition: Maxout单元通过多个线性函数的最大值组合，能够逼近任意凸函数的特性。
source: 第4章 前馈神经网络/Maxout单元.md
---

# Maxout单元

Maxout单元是一种建立在向量输入之上的复杂非线性模块。它包含多组可学习的权重向量和偏置，对输入计算多个净输入并取最大值作为输出，可视为任意凸函数的分段线性近似，整体学习输入到输出的非线性映射。

## 定义与计算机制

<!-- @anchor-begin id="maxout-definition" terms="1" note="Maxout单元的形式化定义与计算过程" -->
与传统的逐元素激活函数不同，**Maxout单元**的输入是上一层神经元的全部原始输出，即一个 $D$ 维向量 $\pmb{x} = [x_1; x_2; \cdots; x_D]$。

每个Maxout单元内部包含 $K$ 组可学习的权重向量 $\pmb{w}_k \in \mathbb{R}^D$ 和偏置 $b_k$（$1 \leq k \leq K$）。对于给定的输入 $\pmb{x}$，Maxout单元首先计算 $K$ 个净输入 $z_k$：
$$
z_k = \pmb{w}_k^\top \pmb{x} + b_k
$$
其中 $\pmb{w}_k = [w_{k,1}, \cdots, w_{k,D}]^\top$ 为第 $k$ 个权重向量。

随后，Maxout单元的非线性函数定义为这 $K$ 个净输入中的最大值：
$$
\operatorname{maxout}(\pmb{x}) = \max_{k \in [1, K]} (z_k)
$$
<!-- @anchor-end -->

## 核心特性与直观理解

<!-- @anchor-begin id="maxout-properties" terms="1|2" note="Maxout单元的本质特性与凸函数近似能力" -->
Maxout单元并非传统意义上仅作用于单个标量净输入的普通激活函数，而是一个自身带有可学习参数（$K$ 组 $\pmb{w}_k$ 和 $b_k$）的复合非线性模块。它整体学习输入到输出之间的非线性映射关系，而不是单纯对净输入做逐元素变换。

从表示能力来看，Maxout单元可以看作**任意凸函数的分段线性近似**。由于它是由多个线性函数取最大值构成的，其几何形态表现为多个超平面的上包络面，因此在有限的点上是不可微的。采用Maxout单元的神经网络通常被称为Maxout网络。
<!-- @anchor-end -->

### 与ReLU变体的联系
Maxout的思想在简单的激活函数变体中也有体现。例如，带泄露的ReLU（Leaky ReLU）在输入 $x \leq 0$ 时保持一个很小的梯度 $\gamma$（$\gamma < 1$），其定义可以改写为：
$$
\operatorname{LeakyReLU}(x) = \max(x, \gamma x)
$$
这实际上相当于一个包含两组参数（$w_1=1, b_1=0$ 和 $w_2=\gamma, b_2=0$）的简单Maxout单元。

## 相关知识点

- [[前馈神经网络计算机制]]{type=topic, label=前馈神经网络计算机制, render=link}
- [[ReLU函数及其变体]]{type=topic, label=ReLU函数及其变体, render=link}