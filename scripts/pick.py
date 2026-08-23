# -*- coding: utf-8 -*-
"""
题库取题工具。给刷题 skill 用，避免把整个题库读进上下文。

  python3 pick.py overview [科目]
  python3 pick.py pick  --科目 人力资源 [--范围 组织行为] [--n 10] [--题型 多选题]
                        [--星级 3] [--真题] [--考点 需要层次理论] [--排除已做]
  python3 pick.py kp 需要层次理论 [--科目 人力资源]
  python3 pick.py weak  --科目 人力资源            # 按错题本算薄弱考点
  python3 pick.py log   --题目ID HR-Q00123 --结果 错 [--我的答案 AB] [--日期 2026-08-23]
  python3 pick.py wrong --科目 人力资源 [--n 10]   # 取错题重做

错题本是 _错题本.md：正文按考点分组、带完整题目和解析，给人看；
文件末尾的 tsv 代码块是做题流水，程序读它，每次 log 会重写整篇正文。
"""
import argparse
import datetime
import glob
import json
import os
import random
import re
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))


def find_bank():
    """找题库根目录。按优先级试几个位置，装在哪都能跑。

    1. $SHUATI_BANK —— 显式指定，最高优先
    2. 脚本旁边的 data/ —— GitHub 仓库布局（scripts/pick.py + data/）
    3. 脚本的上一级 —— 老布局（_题库/_工具/pick.py，数据是 _工具 的兄弟）
    题库的判据是「至少有一个科目目录，里面有 题目/*.jsonl」，不靠目录名。
    """
    cands = []
    env = os.environ.get("SHUATI_BANK")
    if env:
        cands.append(os.path.expanduser(env))
    cands += [os.path.join(ROOT, "data"),
              os.path.join(os.path.dirname(ROOT), "data"),
              os.path.dirname(ROOT),
              os.getcwd()]
    for d in cands:
        if d and glob.glob(os.path.join(d, "*", "题目", "*.jsonl")):
            return os.path.abspath(d)
    raise SystemExit(
        "找不到题库。把题库放到 " + os.path.join(ROOT, "data")
        + " 下（要有 <科目>/题目/*.jsonl），或者设环境变量 SHUATI_BANK 指过去。")


BANK = find_bank()
# 做题记录写在题库旁边；题库只读时用 $SHUATI_HOME 换个可写位置
WRONG = os.path.join(os.environ.get("SHUATI_HOME") or BANK, "_错题本.md")
LEDGER_HEAD = "## 做题流水（程序读，别手改这段）"
SUBJ_ALIAS = {
    "人力": "人力资源", "人力资源": "人力资源", "hr": "人力资源", "专业": "人力资源",
    "基础": "经济基础", "经济基础": "经济基础", "eb": "经济基础", "经基": "经济基础",
}


def subj(s):
    s = (s or "").strip().lower()
    return SUBJ_ALIAS.get(s, s or None)


def load(subject=None):
    out = []
    pat = os.path.join(BANK, subject or "*", "题目", "*.jsonl")
    for fp in sorted(glob.glob(pat)):
        with open(fp, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    out.append(json.loads(line))
    return out


def load_kp(subject=None):
    out = []
    for fp in sorted(glob.glob(os.path.join(BANK, subject or "*", "知识点.jsonl"))):
        with open(fp, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    out.append(json.loads(line))
    return out


def load_log():
    """从 _错题本.md 末尾的流水代码块里读做题记录。

    md 上半部分是给人看的，会被整篇重写；流水这段是唯一的事实来源。
    """
    if not os.path.exists(WRONG):
        return []
    txt = open(WRONG, encoding="utf-8").read()
    tail = txt.split(LEDGER_HEAD)[-1] if LEDGER_HEAD in txt else ""
    out = []
    for line in tail.splitlines():
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) >= 3 and re.match(r"^\d{4}-\d{2}-\d{2}$", parts[0]):
            out.append({"日期": parts[0], "题目ID": parts[1], "结果": parts[2],
                        "我的答案": parts[3] if len(parts) > 3 else ""})
    return out


SRC_RANK = {"章节历年真题": 0, "历年真题卷": 1, "机考真题": 2, "母题": 3,
            "模考预测卷": 4, "新增考点预测题": 5}


def dedup(qs):
    """同一道真题在母题和章节真题里都收了，刷题时只留一份。

    优先留标了考试年份的（真题信息更全），其次按题源可靠性排。
    """
    best = {}
    out = []
    for q in qs:
        gid = q.get("重题组")
        if not gid:
            out.append(q)
            continue
        rank = (0 if q.get("考试年份") else 1,
                SRC_RANK.get(q.get("题源类型"), 9),
                -len(q.get("解析") or ""))
        cur = best.get(gid)
        if cur is None or rank < cur[0]:
            best[gid] = (rank, q)
    return out + [v[1] for v in best.values()]


SRS_INTERVAL = [0, 1, 2, 4, 8, 16, 32, 64]      # 各级复习间隔（天）


def srs(recs):
    """从做题流水推每题的权重和状态。不另存权重表——流水就是唯一事实来源。

    连着答对就升级，间隔翻倍、权重减半；**答错直接打回第 0 级并立刻到期**，
    而且「错过几次」会长期抬高权重——攻克了也比从没错过的题出现得勤。
    间隔没到的题压到很低但不归零（偶尔还是会撞见，跟背单词软件一个道理）。
    """
    hist = defaultdict(list)
    for x in recs:
        hist[x["题目ID"]].append(x)
    today = datetime.date.today()
    out = {}
    for qid, xs in hist.items():
        lvl, n_wrong = 0, 0
        for x in xs:
            if x["结果"] == "对":
                lvl += 1
            else:
                lvl = 0          # 打回重来，别只退两级——退两级会让刚答错的题被间隔压住
                n_wrong += 1
        try:
            last = datetime.date.fromisoformat(xs[-1]["日期"])
            days = max(0, (today - last).days)
        except ValueError:
            days = 99
        want = SRS_INTERVAL[min(lvl, len(SRS_INTERVAL) - 1)]
        ready = 1.0 if want == 0 else min(1.0, days / want)
        w = (0.5 ** lvl) * (1 + 0.8 * n_wrong) * (0.06 + 0.94 * ready)
        out[qid] = {"权重": round(max(w, 0.01), 3), "级别": lvl,
                    "错次": n_wrong, "隔天": days, "到期": ready >= 1.0}
    return out


def srs_tag(st):
    """给取题输出标个简短状态，判卷和讲解时能看出这题是新的还是老错题。"""
    if st is None:
        return "新题"
    s = f"L{st['级别']}"
    if st["错次"]:
        s += f"·错{st['错次']}次"
    s += f"·{st['隔天']}天前" if st["隔天"] else "·今天做过"
    return s


def wsample(items, weights, n):
    """带权不放回抽样（Efraimidis-Spirakis）：key = U^(1/w)，取最大的 n 个。"""
    keyed = []
    for it, w in zip(items, weights):
        u = random.random() or 1e-12
        keyed.append((u ** (1.0 / max(w, 1e-6)), it))
    keyed.sort(key=lambda kv: -kv[0])
    return [it for _, it in keyed[:n]]


def prov(q):
    """这题的出处，给人核对用。没有来源文件字段就用中性描述拼一个。"""
    bits = [q.get("题源类型"), q.get("章节")]
    if q.get("考试年份"):
        bits.append(f"{q['考试年份']}真题")
    if q.get("来源文件"):
        bits = [q["来源文件"]]
    if q.get("源页码"):
        bits.append(f"p{q['源页码']}")
    return " / ".join(b for b in bits if b) or "未记录"


def expl_of(q):
    """解析正文。OCR 常在开头多留一个冒号（'：：本题考查…'），显示时抹掉。"""
    return re.sub(r"^[：:\s　]+", "", q.get("解析") or "")


def fmt(q, show_answer=True, n=None, st=None):
    head = f"[{q['题目ID']}] {q['题型'] or '?'}"
    tags = [t for t in (q.get("知识点"), q.get("章节"),
                        f"{q['考试年份']}真题" if q.get("考试年份") else q.get("题源类型"),
                        srs_tag(st) if show_answer else None)
            if t]
    lines = [f"{'### 第%d题 ' % n if n else ''}{head}  ({' / '.join(tags)})"]
    if q.get("案例材料"):
        lines.append("材料：" + q["案例材料"])
    lines.append("题干：" + (q["题干"] or ""))
    for k in sorted(q["选项"]):
        lines.append(f"  {k}. {q['选项'][k]}")
    if show_answer:
        lines.append("答案：" + (q["答案"] or "?"))
        if q.get("解析"):
            lines.append("解析：" + expl_of(q))
        if q.get("存疑"):
            lines.append("存疑：" + "；".join(q["存疑"]))
    return "\n".join(lines)


def cmd_overview(a):
    with open(os.path.join(BANK, "_索引.json"), encoding="utf-8") as f:
        idx = json.load(f)
    want = subj(a.科目)
    for s, info in idx.items():
        if want and s != want:
            continue
        print(f"== {s} ==  考点 {info['考点数']}，题 {info['题目总数']}"
              f"（可刷 {info['可刷总数']}）")
        for m in info["模块"]:
            print(f"   模块{m['模块号']} {m['模块']}")
        for fn, v in info["题目文件"].items():
            print(f"   - {fn}: {v['题数']} 题（可刷 {v['可刷']}，真题 {v['真题数']}）"
                  f" {v['题型']}")
    log = load_log()
    if log:
        w = {x["题目ID"] for x in log if x.get("结果") == "错"}
        latest = {}
        for x in log:
            latest[x["题目ID"]] = x.get("结果")
        fixed = {i for i in w if latest.get(i) == "对"}
        print(f"\n错题本（_错题本.md）：做过 {len({x['题目ID'] for x in log})} 题，"
              f"错过 {len(w)} 题，待复习 {len(w) - len(fixed)} 题，已攻克 {len(fixed)} 题")


def cmd_pick(a):
    subject = subj(a.科目)
    qs = [q for q in load(subject) if q["质量"] == "可刷"]
    if not a.保留重题:
        qs = dedup(qs)
    if a.范围:
        # 文件名当范围传也认（'试卷_机考真题'、'模块3_劳动经济'），先剥掉前缀
        pat = re.sub(r"^(?:模块\d+|试卷|专项)_?", "", a.范围.strip()) or a.范围.strip()
        mno = re.match(r"^模块(\d+)$", a.范围.strip())
        if mno:
            qs = [q for q in qs if str(q.get("模块号")) == mno.group(1)]
        else:
            qs = [q for q in qs if pat in (q.get("模块") or "")
                  or pat in (q.get("章节") or "")
                  or pat in (q.get("题源类型") or "")
                  or pat in (q.get("所属节") or "")]
    if a.考点:
        qs = [q for q in qs if a.考点 in (q.get("知识点") or "")]
    if a.题型:
        qs = [q for q in qs if q["题型"] == a.题型]
    if a.真题:
        qs = [q for q in qs if q.get("考试年份")]
    if a.星级:
        kp = {k["知识点ID"]: k["考频星级"] for k in load_kp(subject)}
        qs = [q for q in qs if kp.get(q.get("知识点ID"), 0) >= a.星级]
    if a.排除已做:
        done = {x["题目ID"] for x in load_log()}
        qs = [q for q in qs if q["题目ID"] not in done]
    if not qs:
        print("没有符合条件的题。放宽条件试试（去掉 --星级 或 --真题）。")
        return
    random.seed(a.seed)
    state = {} if a.均匀 else srs(load_log())
    if a.均匀:
        random.shuffle(qs)
    else:
        qs = wsample(qs, [state.get(q["题目ID"], {}).get("权重", 1.0) for q in qs],
                     min(len(qs), max(a.n * 4, 40)))
    # 案例题按组抽，材料不能拆开
    picked, seen_case = [], set()
    for q in qs:
        if len(picked) >= a.n:
            break
        cid = q.get("案例组")
        if cid:
            # 老数据的 案例组 只在单个来源文件内唯一，得带上文件名一起当键；
            # 清洗版已经把 案例组 做成全局唯一，没有 来源文件 字段
            key = (q.get("来源文件") or "", cid)
            if key in seen_case:
                continue
            seen_case.add(key)
            group = [x for x in qs if x.get("案例组") == cid
                     and (x.get("来源文件") or "") == (q.get("来源文件") or "")]
            picked.extend(sorted(group, key=lambda x: x.get("卷内题号") or 0))
        else:
            picked.append(q)
    picked = picked[:max(a.n, 1)]
    n_old = sum(1 for q in picked if q["题目ID"] in state)
    print(f"# 抽到 {len(picked)} 题（{subject or '两科'}"
          f"{' / ' + a.范围 if a.范围 else ''}）"
          + ("　抽法：均匀随机" if a.均匀
             else f"　抽法：按权重（复习旧题 {n_old} 道，新题 {len(picked) - n_old} 道）"))
    for i, q in enumerate(picked, 1):
        print()
        print(fmt(q, show_answer=True, n=i, st=state.get(q["题目ID"])))


def cmd_kp(a):
    subject = subj(a.科目)
    kw = a.关键词
    kps = [k for k in load_kp(subject) if kw in k["知识点"]]
    if not kps:
        print(f"没找到含「{kw}」的考点。")
        return
    qs = load(subject)
    by_kp = defaultdict(list)
    for q in qs:
        if q.get("知识点ID"):
            by_kp[q["知识点ID"]].append(q)
    for k in sorted(kps, key=lambda x: -(x["考频星级"] or 0))[:6]:
        print(f"== {k['知识点ID']} {k['知识点']} "
              f"{'★' * k['考频星级'] if k['考频星级'] else '－'} "
              f"近4年{k.get('近4年频次') if k.get('近4年频次') is not None else '?'}次 "
              f"| {k.get('模块')} / {k.get('章节')} / {k.get('所属节') or ''}")
        if k.get("考察年份"):
            print("   考过：" + "、".join(k["考察年份"]))
        print(f"   骨架来源：{k['骨架来源']}，关联题 {len(by_kp.get(k['知识点ID'], []))} 道")
        for q in by_kp.get(k["知识点ID"], [])[:a.n]:
            print()
            print(fmt(q, show_answer=True))
        print()


def cmd_weak(a):
    subject = subj(a.科目)
    log = load_log()
    if not log:
        print("错题本还是空的，先刷几道。")
        return
    qid2kp = {}
    for q in load(subject):
        qid2kp[q["题目ID"]] = (q.get("知识点ID"), q.get("知识点"), q.get("模块"))
    tot, wrong = Counter(), Counter()
    for x in log:
        info = qid2kp.get(x["题目ID"])
        if not info or not info[1]:
            continue
        tot[info[1:]] += 1
        if x.get("结果") == "错":
            wrong[info[1:]] += 1
    rows = [(k, wrong[k], tot[k]) for k in tot if wrong[k]]
    rows.sort(key=lambda r: (-r[1] / r[2], -r[1]))
    print(f"薄弱考点（{subject or '两科'}）：")
    for (name, mod), w, t in rows[:a.n]:
        print(f"  {name}（{mod}） 错 {w}/{t}")


def load_lect(subject=None):
    """考点讲解（可选）。有 考点讲解.jsonl 就用它当知识点正文，没有就退回解析归纳。"""
    out = {}
    for fp in sorted(glob.glob(os.path.join(BANK, subject or "*", "考点讲解.jsonl"))):
        with open(fp, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    out[r["知识点ID"]] = r
    return out


def _dense(s):
    return re.sub(r"[\s　（）()【】，。、；：,.;:]", "", s or "")


def _overlap(a, b):
    """两段话的 8 字片段重合度，用来判断是不是同一段解析的复述。"""
    sa = {a[i:i + 8] for i in range(0, len(a), 8)}
    sb = {b[i:i + 8] for i in range(0, len(b), 8)}
    return len(sa & sb) / max(1, min(len(sa), len(sb)))


TOPIC = re.compile(r"本题考查[的]?[：:]?\s*(.{2,32}?)(?:[。；;\n]|$)")


def topic_of(q):
    """解析开头「本题考查XX」里的 XX。归并兄弟题时用它防串味。"""
    m = TOPIC.search(q.get("解析") or "")
    return _dense(m.group(1)) if m else ""


def same_topic(a_key, sib):
    """兄弟题讲的是不是同一件事。

    有些考点是从「第N章新增考点」这种粗标签挂上来的，同一个知识点ID 下面
    混着好几个真考点（医疗保险待遇 / 伤残待遇 / 工伤待遇）。光按 ID 归并
    会把不相干的解析贴到一起，越读越乱，所以再用「本题考查XX」卡一道。
    """
    t = topic_of(sib)
    if not t or not a_key:
        return True
    return t in a_key or a_key in t


def knowledge_block(q, allq, lect):
    """这道题背后的完整知识点。

    错题本的重点是「把正确的东西记住」，光有本题解析常常只讲被考的那一条。
    同一考点下其他题的解析往往把整张清单列全了（教材原文照抄），所以把兄弟题里
    最全的解析补进来——只补内容不同的，避免同一段话贴三遍。
    """
    lines = []
    rec = lect.get(q.get("知识点ID") or "")
    if rec and rec.get("正文"):
        lines.append(rec["正文"])
    own = expl_of(q)
    if own:
        lines.append(own)
    kid = q.get("知识点ID")
    if kid:
        key = topic_of(q) or _dense(q.get("知识点") or "")
        sib = sorted((expl_of(x) for x in allq
                      if x.get("知识点ID") == kid and x["题目ID"] != q["题目ID"]
                      and x.get("解析") and same_topic(key, x)), key=len, reverse=True)
        have = [_dense(t) for t in lines]
        for t in sib:
            d = _dense(t)
            if len(d) < 60 or any(d in h or h in d or _overlap(d, h) > 0.35
                                  for h in have):
                continue
            lines.append(t)
            have.append(d)
            if len(lines) >= 4:
                break
    return lines


def render_book(recs):
    """按流水重写整本 _错题本.md。

    错题按「错的次数」排前面，同一考点的题聚在一起——复习时一眼看出哪个考点在漏。
    最近一次答对的（之前错过）挪到「已攻克」，留个痕迹但不占正文。
    """
    hist = defaultdict(list)
    for x in recs:
        hist[x["题目ID"]].append(x)
    allq = load()
    qmap = {q["题目ID"]: q for q in allq if q["题目ID"] in hist}
    kpmap = {k["知识点ID"]: k for k in load_kp()}
    lect = load_lect()
    state = srs(recs)

    still, fixed = [], []
    for qid, xs in hist.items():
        q = qmap.get(qid)
        if q is None:
            continue
        n_wrong = sum(1 for x in xs if x["结果"] == "错")
        if not n_wrong:
            continue
        (fixed if xs[-1]["结果"] == "对" else still).append((q, xs, n_wrong))

    L = [f"# 错题本 · 中级经济师（{datetime.date.today()}）", ""]
    done = {x["题目ID"] for x in recs}
    for s in ("人力资源", "经济基础"):
        ids = {q["题目ID"] for q in allq if q["科目"] == s}
        d = done & ids
        if not d:
            continue
        w = sum(1 for q, _, _ in still if q["科目"] == s)
        f_ = sum(1 for q, _, _ in fixed if q["科目"] == s)
        L.append(f"- **{s}**：做过 {len(d)} 题，待复习 {w} 题，已攻克 {f_} 题")
    L.append("")
    L.append("> 这份东西是用来**记正确知识**的，不是用来重做题的——每道题下面的"
             "「要记住的知识点」把同一考点其他题的解析也归并进来了，读完整段比对着"
             "单题解析更管用。要重做题走 `/shuati`，说「复习错题」。")
    L.append("")
    L.append("> 正文由 `pick.py log` 自动重写，手写的批注会被覆盖——"
             "想留笔记写在文件外面。")
    L.append("")

    for s in ("人力资源", "经济基础"):
        rows = [r for r in still if r[0]["科目"] == s]
        if not rows:
            continue
        L += [f"## {s}", ""]
        by_kp = defaultdict(list)
        for r in rows:
            by_kp[r[0].get("知识点") or "（未归考点）"].append(r)
        order = sorted(by_kp.items(),
                       key=lambda kv: (-sum(r[2] for r in kv[1]), kv[0]))
        for name, rs in order:
            k = kpmap.get(rs[0][0].get("知识点ID") or "")
            star = "★" * (k["考频星级"] or 0) if k and k.get("考频星级") else ""
            years = ("，考过 " + "、".join(k["考察年份"])) if k and k.get("考察年份") else ""
            L.append(f"### {name} {star}".rstrip()
                     + f"　<sub>{rs[0][0].get('章节') or ''}{years}</sub>")
            L.append("")
            for q, xs, n_wrong in sorted(rs, key=lambda r: -r[2]):
                mine = "、".join(x["我的答案"] or "?" for x in xs
                                if x["结果"] == "错")
                st = state.get(q["题目ID"])
                L.append(f"**[{q['题目ID']}] {q['题型'] or ''}**"
                         + (f"（{q['考试年份']}真题）" if q.get("考试年份") else "")
                         + f"　错 {n_wrong} 次，我答过 {mine}"
                         + (f"，下次复习在 {SRS_INTERVAL[min(st['级别'], 7)]} 天后"
                            if st and st["级别"] else "，等待重做"))
                L.append("")
                if q.get("案例材料"):
                    L += ["> 材料：" + q["案例材料"], ""]
                L.append(q["题干"] or "")
                L.append("")
                for c in sorted(q["选项"]):
                    right = c in (q["答案"] or "")
                    picked_wrong = (not right) and any(
                        c in (x["我的答案"] or "") for x in xs if x["结果"] == "错")
                    tag = " ✅" if right else (" ←我错选了" if picked_wrong else "")
                    L.append(f"- {c}. {q['选项'][c]}{tag}")
                L.append("")
                L.append(f"**正确答案：{q['答案'] or '?'}**")
                L.append("")
                kb_lines = knowledge_block(q, allq, lect)
                if kb_lines:
                    L.append("**要记住的知识点**")
                    L.append("")
                    for t in kb_lines:
                        L.append("> " + t.replace("\n", " "))
                        L.append(">")
                    L.pop()
                    L.append("")
                L.append("<sub>出处：" + prov(q) + "</sub>")
                L += ["", "---", ""]

    if fixed:
        L += ["## 已攻克", ""]
        for q, xs, n_wrong in sorted(fixed, key=lambda r: -r[2]):
            L.append(f"- [{q['题目ID']}] {q.get('知识点') or q.get('章节') or ''}"
                     f" — 错过 {n_wrong} 次，{xs[-1]['日期']} 答对")
        L.append("")

    L += [LEDGER_HEAD, "", "```tsv"]
    for x in recs:
        L.append("\t".join([x["日期"], x["题目ID"], x["结果"],
                            x.get("我的答案") or ""]))
    L += ["```", ""]

    with open(WRONG, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return len(still), len(fixed)


def cmd_log(a):
    recs = load_log()
    new = {"日期": a.日期 or str(datetime.date.today()),
           "题目ID": a.题目ID, "结果": a.结果, "我的答案": a.我的答案 or ""}
    # 同一天同一题同样的作答重复提交，视为重发而不是又做了一遍
    dup = [i for i, x in enumerate(recs)
           if (x["题目ID"], x["日期"], x["结果"], x.get("我的答案") or "")
           == (new["题目ID"], new["日期"], new["结果"], new["我的答案"])]
    if dup:
        print(f"（{a.题目ID} 今天已经记过同样的作答，不重复计数）")
    else:
        recs.append(new)
    n_still, n_fixed = render_book(recs)
    print(f"已记录 {a.题目ID} {a.结果}"
          + (f"（我答 {a.我的答案}）" if a.我的答案 else "")
          + f" → 错题本待复习 {n_still} 题，已攻克 {n_fixed} 题")


def cmd_wrong(a):
    subject = subj(a.科目)
    log = load_log()
    latest = {}
    for x in log:
        latest[x["题目ID"]] = x.get("结果")
    ids = {k for k, v in latest.items() if v == "错"}
    qs = [q for q in load(subject) if q["题目ID"] in ids]
    if not qs:
        print("没有待复习的错题。")
        return
    state = srs(log)
    # 该复习的排前面：到期的优先，其次错得多的
    qs.sort(key=lambda q: (not state.get(q["题目ID"], {}).get("到期", True),
                           -state.get(q["题目ID"], {}).get("错次", 0),
                           -state.get(q["题目ID"], {}).get("权重", 1)))
    print(f"# 错题重做 {min(len(qs), a.n)} / 共 {len(qs)} 道"
          f"（其中已到复习点 {sum(1 for q in qs if state.get(q['题目ID'], {}).get('到期'))} 道）")
    for i, q in enumerate(qs[:a.n], 1):
        print()
        print(fmt(q, show_answer=True, n=i, st=state.get(q["题目ID"])))


def cmd_logs(a):
    """一次记一批：logs HR-Q01553:对:B EB-Q00892:错:ABC ...

    批量出题时用这个，别循环调 log —— 每次 log 都要重写整本错题本。
    """
    recs = load_log()
    today = a.日期 or str(datetime.date.today())
    added, skipped = 0, 0
    for item in a.记录:
        parts = item.split(":")
        if len(parts) < 2 or parts[1] not in ("对", "错"):
            print(f"跳过格式不对的 {item!r}（要 题目ID:对/错[:我的答案]）")
            continue
        new = {"日期": today, "题目ID": parts[0], "结果": parts[1],
               "我的答案": parts[2] if len(parts) > 2 else ""}
        if any((x["题目ID"], x["日期"], x["结果"], x.get("我的答案") or "")
               == (new["题目ID"], new["日期"], new["结果"], new["我的答案"])
               for x in recs):
            skipped += 1
            continue
        recs.append(new)
        added += 1
    n_still, n_fixed = render_book(recs)
    print(f"记了 {added} 条"
          + (f"，跳过重复 {skipped} 条" if skipped else "")
          + f" → 错题本待复习 {n_still} 题，已攻克 {n_fixed} 题")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("overview"); o.add_argument("科目", nargs="?")
    o.set_defaults(fn=cmd_overview)

    k = sub.add_parser("pick")
    k.add_argument("--科目"); k.add_argument("--范围"); k.add_argument("--考点")
    k.add_argument("--题型"); k.add_argument("--星级", type=int)
    k.add_argument("--n", type=int, default=10)
    k.add_argument("--真题", action="store_true")
    k.add_argument("--排除已做", action="store_true")
    k.add_argument("--保留重题", action="store_true")
    k.add_argument("--均匀", action="store_true", help="关掉权重，纯随机抽")
    k.add_argument("--seed", type=int, default=None)
    k.set_defaults(fn=cmd_pick)

    kp = sub.add_parser("kp"); kp.add_argument("关键词")
    kp.add_argument("--科目"); kp.add_argument("--n", type=int, default=3)
    kp.set_defaults(fn=cmd_kp)

    w = sub.add_parser("weak"); w.add_argument("--科目")
    w.add_argument("--n", type=int, default=15); w.set_defaults(fn=cmd_weak)

    lg = sub.add_parser("log"); lg.add_argument("--题目ID", required=True)
    lg.add_argument("--结果", required=True, choices=["对", "错"])
    lg.add_argument("--我的答案"); lg.add_argument("--日期")
    lg.set_defaults(fn=cmd_log)

    lgs = sub.add_parser("logs")
    lgs.add_argument("记录", nargs="+", help="题目ID:对/错[:我的答案]")
    lgs.add_argument("--日期"); lgs.set_defaults(fn=cmd_logs)

    wr = sub.add_parser("wrong"); wr.add_argument("--科目")
    wr.add_argument("--n", type=int, default=10)
    wr.add_argument("--seed", type=int, default=None); wr.set_defaults(fn=cmd_wrong)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
