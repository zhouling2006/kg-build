---
title: Swish与GELU函数
id: b5d7353a-23c5-4c5f-a443-0a6d83a7592e
type: topic
aliases:
  - Swish与GELU激活函数
tags:
  - 深度学习
  - 激活函数
  - 神经网络
description: 介绍Swish与GELU两种基于软性门控机制的现代激活函数的定义、性质及高效近似方法。
terms:
  - id: 1
    name: Swish函数
    aliases:
      - SiLU
    definition: 一种自门控激活函数，定义为输入值与Logistic函数的乘积，具有非单调性。
  - id: 2
    name: GELU函数
    aliases:
      - 高斯误差线性单元
    definition: 一种通过标准正态分布累积分布函数作为门控权重的激活函数。
  - id: 3
    name: 软性门控机制
    aliases:
      - 自门控机制
    definition: 利用S型函数将输入映射到(0,1)区间，作为权重控制信息通过量的非线性机制。
source: 第4章 前馈神经网络/Swish与GELU函数.md
---

# Swish与GELU函数

Swish与GELU是现代深度学习中广泛使用的两种逐元素激活函数，它们均通过引入软性门控机制来调整输出，在Transformer等现代大模型中表现优异。与ReLU等简单分段线性函数不同，这两种函数在负半轴保留了平滑的非线性特性，能够更好地拟合复杂的数据分布。

## Swish函数

<!-- @anchor-begin id="swish-def" terms="1|3" note="Swish函数定义与自门控机制" -->
**Swish函数**是一种自门控（Self-Gated）激活函数，其数学定义为：
$$
\operatorname{swish}(x) = x \sigma(\beta x)
$$
其中，$\sigma(\cdot)$ 为Logistic函数（Sigmoid型函数），$\beta$ 为可学习的参数或一个固定的超参数。

在Swish函数中，$\sigma(\beta x) \in (0, 1)$ 起到了一种**软性门控机制**的作用：
- 当 $\sigma(\beta x)$ 接近于1时，门处于“开”状态，激活函数的输出近似于 $x$ 本身；
- 当 $\sigma(\beta x)$ 接近于0时，门的状态为“关”，激活函数的输出近似于0。
<!-- @anchor-end -->

参数 $\beta$ 的取值直接决定了Swish函数的形态，使其可以看作线性函数和ReLU函数之间的非线性插值函数：
- 当 $\beta = 0$ 时，Swish函数退化为线性函数 $x / 2$。
- 当 $\beta = 1$ 时，Swish函数在 $x > 0$ 时近似线性，在 $x < 0$ 时近似饱和，同时具有一定的**非单调性**。此时，Swish函数也被称为 **SiLU**（Sigmoid Linear Unit）。
- 当 $\beta \to +\infty$ 时，$\sigma(\beta x)$ 趋向于离散的0-1函数，Swish函数近似为ReLU函数。

![](../images/c82e6fa6f3996b64c660157ef82681228e44ac7e36c43922bc529ee3ffe72c10.jpg)（描述：函数图像图，展示了不同β值下Swish函数的曲线变化）
图 4.9 Swish 函数

## GELU函数

<!-- @anchor-begin id="gelu-def" terms="2|3" note="GELU函数定义与门控机制" -->
**GELU**（Gaussian Error Linear Unit，高斯误差线性单元）也是一种通过门控机制来调整其输出值的激活函数。其数学定义为：
$$
\operatorname{GELU}(x) = x \Phi(x)
$$
其中，$\Phi(x)$ 是标准正态分布 $\mathcal{N}(0,1)$ 的累积分布函数（CDF）。与Swish函数类似，GELU利用 $\Phi(x)$ 这个S型函数作为门控权重，决定输入 $x$ 的通过比例。
<!-- @anchor-end -->

### 高效近似计算

由于标准正态分布的累积分布函数 $\Phi(x)$ 在实际计算中涉及误差函数（erf），计算成本较高。由于高斯分布的累积分布函数为S型函数，实际应用中常采用Tanh函数或Logistic函数对其进行高效近似：

1. **Tanh近似**：
$$
\operatorname{GELU}(x) \approx 0.5 x \left( 1 + \tanh \left( \sqrt{\frac{2}{\pi}} (x + 0.044715 x^3) \right) \right)
$$

2. **Logistic近似**：
$$
\operatorname{GELU}(x) \approx x \sigma(1.702 x)
$$

### 与Swish函数的对比

当使用Logistic函数近似高斯CDF时，GELU的形式在数值上与特定参数（如 $\beta \approx 1.702$）的Swish函数非常接近。然而，两者的设计动机不同：Swish是通过搜索发现的具有优良性能的经验公式，而GELU则是从概率分布的角度对神经元输入进行随机正则化推导而来。在实际工程中，不少深度学习框架同时提供基于误差函数（erf）的精确实现和上述近似实现，开发者可根据数值效率和硬件兼容性进行选择。

## 相关知识点

[[ReLU函数及其变体]]{type=topic, label=ReLU函数及其变体, render=link}
[[Sigmoid型函数]]{type=topic, label=Sigmoid型函数, render=link}
[[门控线性单元]]{type=topic, label=门控线性单元, render=link}