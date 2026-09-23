# -*- coding: utf-8 -*-
# @Time    : 2021/10/12 22:49
# @Author  : Yong Cao
# @Email   : yongcao_epic@hust.edu.cn
import os
import re
import requests
from utils import downLoad_paper

_HEADERS = {"User-Agent": "Mozilla/5.0"}
_ARNUMBER_CACHE = {}


def _doi_to_arnumber(doi):
    """通过 doi.org 的 302 跳转解析出 IEEE 文献号(arnumber)

    例: 10.1109/TNSE.2026.3728880
        -> Location: https://ieeexplore.ieee.org/document/11672286/
    """
    if doi in _ARNUMBER_CACHE:
        return _ARNUMBER_CACHE[doi]
    arnumber = None
    try:
        r = requests.get("https://doi.org/" + doi, headers=_HEADERS,
                         allow_redirects=False, timeout=20)
        m = re.search(r'/document/(\d+)', r.headers.get("Location", ""))
        if m:
            arnumber = m.group(1)
    except Exception as e:
        print("DOI 解析失败:", doi, e)
    _ARNUMBER_CACHE[doi] = arnumber
    return arnumber


def _to_filename(title, limit=150):
    """把论文标题转成合法的 PDF 文件名, 并去掉结尾多余标点"""
    name = re.sub(r'[\\/:*?"<>|{}]', '', title).strip()
    # 新版引用格式里逗号在引号内, 会产生 "...Communications," 这样的结尾
    name = re.sub(r'[\s,;.\-–—]+$', '', name)
    return name[:limit]


def parse_bibtex(bib_file):
    """解析 IEEE BibTeX 导出文件, 返回 [(arnumber, title, year), ...]"""
    with open(bib_file, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()

    # 每条记录以 @TYPE{arnumber, 开头
    # 注意: 上一条的结束大括号 } 和下一个 @ 可能挤在同一行, 所以按 @ 切分
    chunks = re.split(r'(?=@\w+\{)', text)
    records = []
    for chunk in chunks:
        m = re.match(r'@\w+\{(\d+),', chunk)
        if not m:
            continue
        arnumber = m.group(1)

        # 提取标题
        title = ""
        for line in chunk.split("\n"):
            line = line.strip()
            if line.startswith("title"):
                title = line.split("=", 1)[1].strip().rstrip(",")
                title = title.strip("{}")
                break
        if not title:
            continue

        # 提取年份
        ym = re.search(r'\byear\s*=\s*\{(\d{4})\}', chunk)
        year = ym.group(1) if ym else ""
        records.append((arnumber, title, year))
    return records


def organize_info_by_txt(dst_dir, url_file, paper_name_with_year=None):
    if not os.path.exists(url_file):
        return False, None

    with open(url_file, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    # 1) BibTeX 文件以 @ 开头
    if content.lstrip().startswith("@"):
        return _organize_by_bibtex(dst_dir, url_file, paper_name_with_year)

    # 2) IEEE 2026 新版 Plain Text 导出: 引用行内含 doi, 但没有 URL/arnumber
    if "doi:" in content and "arnumber" not in content:
        return _organize_by_plaintext(dst_dir, url_file, paper_name_with_year)

    # 3) 否则沿用 IEEE 老版 txt 逻辑
    return _organize_by_old_txt(dst_dir, url_file, paper_name_with_year)


def _organize_by_bibtex(dst_dir, bib_file, paper_name_with_year=None):
    paper_info = {}
    for i, (arnumber, title, year) in enumerate(parse_bibtex(bib_file)):
        name = _to_filename(title)
        if paper_name_with_year and year:
            name = year + ' ' + name
        paper_info[i] = {
            'name': os.path.join(dst_dir, name + '.pdf'),
            'url': "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber="
                   + arnumber + "&ref="
        }
    return True, paper_info


def _organize_by_plaintext(dst_dir, txt_file, paper_name_with_year=None):
    """解析 IEEE 2026 新版 Plain Text 导出

    该格式每条记录只有 1~2 行(空行分隔), 引用行末尾内嵌 doi, 没有 URL 行,
    因此需要先解析出 DOI, 再通过 doi.org 换取 arnumber 来拼下载链接。
    """
    with open(txt_file, "r", encoding="utf-8", errors="ignore") as f:
        records = [r.strip() for r in f.read().split("\n\n") if r.strip()]

    rule = r'"(.*?)"'
    paper_info = {}
    print("纯文本模式: 共 {} 条记录, 正在解析 DOI 获取文献号...".format(len(records)))
    for i, rec in enumerate(records):
        citation = rec.split("\n")[0]

        titles = re.findall(rule, citation)
        if not titles:
            continue
        name = _to_filename(titles[0])

        if paper_name_with_year:
            years = re.findall(r'\b((?:19|20)\d{2})\b', citation)
            if years:
                name = years[-1] + ' ' + name

        dm = re.search(r'doi:\s*(10\.\d{4,9}/[^\s,]+)', citation)
        if not dm:
            print("  跳过(未找到 DOI):", citation[:60])
            continue
        doi = dm.group(1).rstrip('.')

        arnumber = _doi_to_arnumber(doi)
        if not arnumber:
            print("  跳过(无法解析文献号):", doi)
            continue

        print("  [{}/{}] {} -> {}".format(i + 1, len(records), doi, arnumber))
        paper_info[i] = {
            'name': os.path.join(dst_dir, name + '.pdf'),
            'url': "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber="
                   + arnumber + "&ref="
        }
    print("纯文本模式: 成功解析 {} 篇".format(len(paper_info)))
    return True, paper_info


def _organize_by_old_txt(dst_dir, url_file, paper_name_with_year=None):
    with open(url_file, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.read().split("\n\n")
    rule = r'"(.*?)"'
    rstr = r"[\=\(\)\,\/\\\:\*\?\？\"\<\>\|\'\']"
    paper_info = {}
    for i, line in enumerate(lines):
        content = line.split("\n")
        slotList = re.findall(rule, content[0])
        if not slotList:
            continue
        papername = re.sub(rstr, '', slotList[0])
        if paper_name_with_year and len(content) > 1:
            parts = content[1].split(".")
            if len(parts) > 2 and parts[2].strip().isdigit():
                papername = parts[2].strip() + ' ' + papername
        papername = os.path.join(dst_dir, papername + '.pdf')
        # paper url
        if len(content) > 3 and "URL" in content[3]:
            arnumber = \
                content[3].replace("URL: http://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=", "").split("&")[0]
            paper_info[i] = {'name': papername,
                             'url': "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber="
                                    + arnumber + "&ref="}
    return True, paper_info


if __name__ == '__main__':
    # 配置存储文件夹
    dst_dir = "./save"
    if not os.path.exists(dst_dir):
        os.mkdir(dst_dir)
    # 封装下载url和论文名称
    url_txt = "url.txt"
    paper_info = organize_info_by_txt(dst_dir, url_txt, True)
    # 下载论文
    downLoad_paper(paper_info)
