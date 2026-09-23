# -*- coding: utf-8 -*-
# @Time    : 2021/10/13 12:16 
# @Author  : Yong Cao
# @Email   : yongcao_epic@hust.edu.cn
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests


def _init():
    # 初始化一个全局的字典
    global _global_dict
    _global_dict = {}


def set_value(key, value):
    _global_dict[key] = value


def get_value(key):
    try:
        return _global_dict[key]
    except KeyError as e:
        print(e)



def get_window_size(win, update=True):
    """ 获得窗体的尺寸 """
    if update:
        win.update()
    return win.winfo_width(), win.winfo_height(), win.winfo_x(), win.winfo_y()


def center_window(win, width=None, height=None):
    """ 将窗口屏幕居中 """
    screenwidth = win.winfo_screenwidth()
    screenheight = win.winfo_screenheight()
    if width is None:
        width, height = get_window_size(win)[:2]
    size = '%dx%d+%d+%d' % (width, height, (screenwidth - width) / 2, (screenheight - height) / 3)
    win.geometry(size)


# ---------------- 可调参数 ----------------
MAX_WORKERS = 3        # 并发下载数。1=串行; 3 相对安全; 被限流时请调小
REQUEST_GAP = 1.0      # 每篇请求之间的间隔(秒), 太小容易被限流
MAX_RETRY = 3          # 被限流后的重试次数
BACKOFF_BASE = 30      # 退避基数(秒): 依次等待 30 / 60 / 120
# ------------------------------------------

# 模拟浏览器请求头, 否则 IEEE 会返回 418/420 拦截页面
BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,'
              'image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

_thread_local = threading.local()


def _new_session():
    """新建一个带 IEEE cookie 的会话

    必须先访问一次首页拿到 cookie, 否则直接请求 PDF 接口会被判为机器人(HTTP 418)。
    """
    s = requests.Session()
    try:
        s.get("https://ieeexplore.ieee.org/", headers=BROWSER_HEADERS, timeout=30)
    except Exception as e:
        print("初始化 IEEE 会话失败:", e)
    return s


def get_session():
    """获取当前线程的会话(requests.Session 非线程安全, 故每个线程一份)"""
    if not hasattr(_thread_local, "session"):
        _thread_local.session = _new_session()
    return _thread_local.session


def is_valid_pdf(path):
    """通过文件头判断是否为真正的 PDF"""
    try:
        if os.path.getsize(path) < 1024:
            return False
        with open(path, 'rb') as f:
            return f.read(5) == b'%PDF-'
    except Exception:
        return False


def _pdf_headers():
    headers = dict(BROWSER_HEADERS)
    headers['Referer'] = "https://ieeexplore.ieee.org/"
    headers['Accept'] = 'application/pdf,*/*'
    return headers


def _fetch_pdf(paperurl, papername):
    """下载单篇论文, 返回 (是否成功, 失败原因)

    IEEE 会对高频请求限流(HTTP 420/429, 或返回 HTML 拦截页),
    这里检测到后会自动退避等待并重试, 避免像以前那样直接把拦截页存成 PDF。
    """
    for attempt in range(MAX_RETRY + 1):
        try:
            r = get_session().get(paperurl, headers=_pdf_headers(), timeout=(10, 90))
        except Exception as e:
            if attempt < MAX_RETRY:
                wait = BACKOFF_BASE * (2 ** attempt)
                print("\n请求异常({}), {}s 后重试 ({}/{})".format(e, wait, attempt + 1, MAX_RETRY))
                time.sleep(wait)
                continue
            return False, "请求异常: {}".format(e)

        # 只有真正的 PDF 才写盘
        if r.content[:5] == b'%PDF-':
            with open(papername, 'wb') as f:
                f.write(r.content)
            return True, None

        # 非 PDF: 基本可以确定被限流了
        if attempt < MAX_RETRY:
            wait = BACKOFF_BASE * (2 ** attempt)
            print("\n被限流(HTTP {}), 等待 {}s 后重试 ({}/{})".format(
                r.status_code, wait, attempt + 1, MAX_RETRY))
            time.sleep(wait)
            _thread_local.session = _new_session()   # 换会话刷新 cookie
            continue
        return False, "HTTP {} (未获取到PDF, 多半是被限流)".format(r.status_code)
    return False, "重试次数用尽"


def downLoad_paper(paper_info, show_bar=False, max_workers=None):
    if max_workers is None:
        max_workers = MAX_WORKERS
    print("\n" * 2)
    print("执行开始".center(len(paper_info) + 28, '-'))
    start = time.perf_counter()
    total = len(paper_info)

    # 过滤掉已存在的有效 PDF(避免把之前下坏的网页当成已下载)
    tasks = {}
    already_exist = 0
    for item in paper_info:
        name = paper_info[item]['name']
        if os.path.exists(name) and is_valid_pdf(name):
            already_exist += 1
        else:
            tasks[item] = paper_info[item]

    if not tasks:
        set_value("progress_bar_num", total)
        print("全部 {} 篇已存在, 无需下载。".format(already_exist))
        return True, 0, already_exist

    lock = threading.Lock()
    state = {'done': 0, 'downloaded': 0, 'failed': 0, 'last_reason': ''}

    def worker(item):
        info = tasks[item]
        ok, reason = _fetch_pdf(info['url'], info['name'])
        with lock:
            state['done'] += 1
            if ok:
                state['downloaded'] += 1
            else:
                state['failed'] += 1
                state['last_reason'] = reason
                print("\n下载失败({}): {}".format(reason, info['name']))
            done = state['done']
            set_value("progress_bar_num", already_exist + done)
        c = (already_exist + done) / total * 100
        t = time.perf_counter() - start
        print("\r任务进度:{:>3.0f}% [{}{}]消耗时间:{:.2f}s".format(
            c, '*' * done, '.' * (len(tasks) - done), t), end="")
        time.sleep(REQUEST_GAP)

    if max_workers <= 1:
        print("串行下载中, 共 {} 篇...".format(len(tasks)))
        for item in tasks:
            worker(item)
    else:
        print("并发 {} 路下载中, 共 {} 篇(若被限流会自动退避, 进度条可能暂停, 请勿重复点击下载)...".format(
            max_workers, len(tasks)))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            list(ex.map(worker, tasks))

    set_value("progress_bar_num", total)
    elapsed = time.perf_counter() - start
    succeed = state['failed'] == 0

    print("\n" + "执行结束".center(len(paper_info) + 28, '-'))
    print("-" * 50)
    print("Downloaded {} papers, {} already exists, {} failed. 耗时 {:.1f}s".format(
        state['downloaded'], already_exist, state['failed'], elapsed))
    if state['failed']:
        print("提示: 失败原因多为被 IEEE 限流(HTTP 420/429)。")
        print("      请等待 10~30 分钟后再试, 并把 utils.MAX_WORKERS 调小(如 1)、")
        print("      utils.REQUEST_GAP 调大(如 3), 已下载成功的会自动跳过。")
    print("-" * 50)
    return succeed, state['downloaded'], already_exist