# -*- coding: utf-8 -*-
"""路由闸：把"像不像问句"和"是不是在问我"分开。

这道闸曾经是「只要出现 什么/怎么/为什么 就当在问我」。用真实环境音（LOL 解说、
鱼油广告、政论视频，953 条）实测，它一个人贡献了 95% 的误放行 —— 但它同时也扛着
89% 的正确召回，所以不能删，只能收紧。

收紧后（同一套数据）：误放行 25.4% -> 14.1%，召回仍然 100%。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))

import router  # noqa: E402


AMBIENT = [
    '什么都没交啊，这里反而是小薇，这波大树也没有大招没有后续留人手段',
    '这个阵容又是一个破格阵，但是他三条兵线如果出不去就很难像米之岛强调的那样',
    '感觉不到什么变化就开始骂鱼油是智商税，但其实不是鱼油没用',
    '事情已经发生了，举报也已经进入调查阶段了',
    '野区现在有点过不来，视野不是特别多，就是慢慢的跟你磨',
]


def test_ambient_talk_is_not_a_question():
    """环境音里的疑问词是虚的 —— "什么/怎么"在中文里多数时候不是提问。"""
    for t in AMBIENT:
        assert router.classify(t) == 'skip', t


def test_real_interview_questions_are_kept():
    """真问题一条都不能丢 —— 漏一条比多答一条亏得多。"""
    for t in [
        '你这些里面的降级策略，比如说哪一些你做了降级？',
        '那你为什么刚毕业就要换一家公司？',
        '是哪个项目前面的前置工作。',
        '介绍下你最近做的一个项目',
        '你还有什么需要提问的吗？',
    ]:
        assert router.classify(t) != 'skip', t


def test_explicit_rule_beats_catch_all():
    """命中明确规则（设计/行为/动机/反问）就是提问，不看兜底。

    踩过：收紧兜底后「说一次你做得比较失败的项目」被判 skip ——
    它不含任何疑问词，可它明明命中 BEHAVIOR 的「失败」。
    兜底和明确规则是**或**的关系，不是先后关系。
    """
    t = '说一次你做得比较失败的项目，你从里面学到了什么'
    assert router.is_skip(t) is False
    assert router.classify(t) == 'behavior'


def test_short_noise_is_skipped():
    for t in ['嗯', '好的', '.,', '必了.']:
        assert router.is_skip(t) is True, t
