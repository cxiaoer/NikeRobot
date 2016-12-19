# coding:utf-8

"""
redis (2.10.5)
requests (2.9.1)
python (3.5.2)

"""

import requests
import logging
import json
import time
from threading import Thread
import re
# import redis
import sys
import random
import pdb

logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s  '
                           '%(threadName)s  '
                           '%(levelname)s  '
                           '%(message)s')
LOG = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_5) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/51.0.2704.106 Safari/537.36'
}
# 登陆check 地址
LOGIN_URL = 'https://www.nike.com/profile/login?Content-Locale=zh_CN'
# 提交订单地址
PUT_ORDER_URL = 'https://secure-store.nike.com/ap/services/' \
                'jcartService?qty=1&rt=json&view=3&'
MAX_FAIL_TIMES = 100  # 每个下单线程的最大失败重试次数
MAX_RETRY_TIMES = 200  # 每个下单线程的重新提交订单次数
# 是否开启调试模式
DEBUG = False

# 全局session
session = requests.Session()

# 添加购物车订阅实例
# r = redis.StrictRedis('localhost', 6379)
# p = r.pubsub()
is_add_cart_success = False


class NikeLoginParam(object):
    """登录时, 请求body体参数"""

    def __init__(self, username, password, client_id):
        super(NikeLoginParam, self).__init__()
        self.login = username
        self.password = password
        # self.client_id = client_id
        # self.grant_type = 'password'
        self.rememberMe = True
        # self.ux_id = 'com.nike.commerce.nikedotcom.web'

    def to_json(self):
        return json.dumps(self, default=lambda o: o.__dict__,
                          sort_keys=True, indent=4)

    def __repr__(self):
        return '此次登陆用户名:%s, 密码:%s' % (self.login, self.password)


#
# class AddToCartParam(object):
#     """下单请求参数"""
#
#     def __init__(self, product_id, price, sku_id, line1, line2):
#         super(AddToCartParam, self).__init__()
#         self.productId = product_id
#         self.price = price
#         self.skuId = sku_id
#         self.skuAndSize = '%s:43' % sku_id
#         self.line1 = line1
#         self.line2 = line2


class ShoeInfo(object):
    """鞋子商品信息类"""

    def __init__(self, info_url, info_id, shoe_color):
        super(ShoeInfo, self).__init__()
        self.info_url = info_url
        self.info_id = info_id
        self.shoe_color = shoe_color

    def __repr__(self):
        return '鞋子ID: %s \n 鞋子颜色: %s \n 鞋子地址:%s ' % (self.info_id,
                                                     self.shoe_color,
                                                     self.info_url)


class RegexMatcher(object):
    """正则匹配工具类"""

    # groups = []

    # value_dict_list = []

    def __init__(self, regex):
        self.groups = []
        self.value_dict_list = []
        self.capture_group_name_reg = re.compile('\(\?P<(?P<group_name>.*?)>')
        self.__parse_reg(regex)
        self.regex = re.compile(regex)

    def __parse_reg(self, regex):
        for match in self.capture_group_name_reg.finditer(regex):
            self.groups.append(match.group('group_name'))

    def match(self, content):
        if content is None:
            raise AssertionError
        for match in self.regex.finditer(content):
            d = {}
            for group in self.groups:
                d[group] = match.group(group)
            if len(d) > 0:
                self.value_dict_list.append(d)
        return self

    def get_value(self, group_name):
        if group_name not in self.groups:
            raise KeyError
        result = []
        if DEBUG:
            pdb.set_trace()
        for d in self.value_dict_list:
            result.append(d[group_name])
        if len(result) == 0:
            raise MatchNoResult
        return result[0]

    def get_values(self, group_name):
        return self.value_dict_list

    def find_with_arg(self, **kwargs):
        for d in self.value_dict_list:
            is_find = True
            for k, v in kwargs.items():
                if d[k] != v:
                    is_find = False
                    break
            if is_find:
                return d
        return None

    def __str__(self):
        return str(self.value_dict_list)


class MatchNoResult(Exception):
    """docstring for MatchNoResult"""
    pass


class AddToCartTask(Thread):
    """添加到购物车线程,主要负责鞋子添加到购物车中, 抢鞋的时候,添加购物车成功基本就算抢到了"""

    def __init__(self, param, pd_url):
        super(AddToCartTask, self).__init__()
        self.param = param
        self.pd_url = pd_url

    def run(self):
        global is_add_cart_success
        retry_times = 1  # 尝试提交订单次数

        HEADERS['Referer'] = self.pd_url  # 带上Referer试试能否成功, 不带这个头, 会一直报429
        # p.subscribe('addToCart-notice-channel')
        while True:
            if is_add_cart_success:
                break
            start = time.time()
            LOG.info('[第%s次] 开始尝试添加到购物车了', retry_times)
            add_to_cart_url = PUT_ORDER_URL + \
                              '_=' + str(int(1000 * time.time()))
            if DEBUG:
                pdb.set_trace()
            res = session.get(
                add_to_cart_url, params=self.param, headers=HEADERS)
            LOG.info(res.url)
            end = time.time()
            status_code = res.status_code
            # r.publish('addToCart-notice-channel', 'SUCCESS')
            if status_code == 200:
                res_status_pattern = re.compile('"status" :"(?P<status>.*?)"')
                LOG.info(res.text)
                res_status_match = res_status_pattern.search(res.text)
                if res_status_match:
                    res_status = res_status_match.group('status')
                    if res_status == 'success':  # 添加到购物车成功
                        LOG.info('恭喜添加购物车成功.......; 耗时%s秒', (end - start))
                        is_add_cart_success = True
                        # r.publish('addToCart-notice-channel', 'SUCCESS')
                        break
                    elif res_status == 'wait':  # 下单需要排队,继续下单
                        random_wait_time = random.uniform(2.0, 3.0)
                        LOG.info("排队中......, %.2f 秒后继续重试", random_wait_time)
                        time.sleep(random_wait_time)
                    elif res_status == 'failure':
                        # 截取失败信息
                        fail_message_pattern = re.compile('message"\s:"(.*?)"')
                        fail_message_match = fail_message_pattern.search(
                            res.text)
                        if fail_message_match:
                            LOG.info('添加购物车失败; 原因:%s',
                                     fail_message_match.group(1))
                            # r.publish('addToCart-notice-channel', 'FAIL')
                            sys.exit(1)
            LOG.info('提交订单异常[%s]!', status_code)
            time.sleep(1)  # 隔一秒进行重新提交
            retry_times += 1


# 登陆获取关键token参数
def login(param):
    LOG.info('开始登陆Nike官网了.......')
    session.get('http://www.nike.com/cn/zh_cn')
    start = time.time()
    res = session.post(LOGIN_URL, data=param.__dict__, headers=HEADERS)
    end = time.time()
    status_code = res.status_code
    if status_code == 200:
        if DEBUG:
            pdb.set_trace()
            # key_token = json.loads(content)['access_token']
            # if json.loads(content)['access_token'] is not None:
            # LOG.info(res.cookies)
        LOG.info('登陆Nike官网成功,耗时%s秒', (end - start))
    else:
        # key_token = None
        LOG.error('登陆Nike官网失败[%s]!', status_code)
        sys.exit(1)


# 清洗html; 去掉空格以及script标签
def clean_html(html_content):
    return html_content


def get_order_param(product_index_url):
    param_d = {'callback': 'nike_Cart_handleJCartResponse',
               'passcode': 'null', 'sizeType': 'null'}

    # 从url 中取出默认的pid
    selected_pid = RegexMatcher('\/pid-(?P<selected_pid>.*?)\/'). \
        match(product_index_url).get_value('selected_pid')
    LOG.info('默选pid: %s', selected_pid)
    # 用户选择颜色
    pd_content = session.get(product_index_url).text
    # LOG.info(pd_content)
    pd_area = RegexMatcher('<div class="color-chips">(?P<area>[\s\S]*?'
                           'data-status="IN_STOCK"[\s\S]*?)</div>'). \
        match(pd_content).get_value('area')
    # LOG.debug('产品区域文本:%s', pd_area)
    in_stock_matcher = RegexMatcher('<a\shref="(?P<pd_url>'
                                    '[\s\S]*?)"'
                                    '\sdata-productid="'
                                    '(?P<pid>\d+)"\stitle="(?P<pd_color>'
                                    '[\s\S]*?)"[\s\S]*?<\/a>').match(pd_area)
    LOG.info('提取出的在售信息:%s', in_stock_matcher)
    user_select_pid = input('请输入你要选择的pid: ')
    if user_select_pid != selected_pid:
        new_pd_url = in_stock_matcher.find_with_arg(
            pid=user_select_pid, )['pd_url']
        pd_content = session.get(new_pd_url).text  # 重新获取一次其他参数
    # 构造订单参数
    param_area = RegexMatcher('<form\s*?action=""\s*?method="post"\s*?'
                              'class="add-to-cart-form nike-buying-tools">'
                              '(?P<param_area>[\s\S]*?)</form>'). \
        match(pd_content). \
        get_value('param_area')
    # LOG.debug('提交订单参数区:%s', param_area)
    # 通用参数
    common_param_matcher = RegexMatcher('<input\s*?type="hidden"\s*?name="'
                                        '(?P<key>.*?)"'
                                        '\s*?(value="(?P<value>.*?)")?\s*?/>'). \
        match(param_area)
    LOG.debug('通用参数信息:%s', common_param_matcher)

    for d in common_param_matcher.get_values(''):
        if len(d) is not 2:
            continue
        value = d['value']
        if value is None or len(value) == 0:
            value = 'null'
        param_d[d['key']] = value
    LOG.info(str(param_d))
    # 用户选择尺码和大小
    size_matcher = RegexMatcher('<option\s(?:class=\"(?P<extra>.*?)\")?\s'
                                '*?name=\"skuId\"\s*?value=\"'
                                '(?P<sku_id>[\s\S]*?):(?P<size>[\s\S]*?)\"'). \
        match(param_area)
    # 去掉售罄的尺码; 格式输出稍微舒服点
    for d in size_matcher.get_values(''):
        extra = d['extra']
        if extra is not None and 'selectBox-disabled' in extra:
            continue
        LOG.info('目前还有货的尺码大小: %s; sku_id: %s', d['size'], d['sku_id'])
    # size_matcher.get_values('')
    sku_id = input('请选择你需要尺码对应的sku_id: ')
    d = size_matcher.find_with_arg(sku_id=sku_id, )
    LOG.info(str(d))
    param_d['skuId'] = sku_id
    param_d['displaySize'] = d['size']  # 有可能要改?????
    param_d['skuAndSize'] = '%s:%s' % (sku_id, d['size'])

    return param_d


if __name__ == '__main__':
    user_name = input('请输入你的nike用户名:')
    password = input('请输入你的nike密码:')
    nike_login_param = NikeLoginParam(
        user_name, password, '')
    login(nike_login_param)
    pd_url = input('请输入鞋子地址:')
    order_param = get_order_param(pd_url)
    threads = []
    for i in range(1):
        thread = AddToCartTask(order_param, pd_url)
        thread.start()
    for thread in threads:
        thread.join()
        # get_order_param(
        #     'http://store.nike.com/cn/zh_cn/pd/zoom-all-out-low-%E7%94%B7%E5%AD%90%E8%B7%91%E6%AD%A5%E9%9E%8B/pid-11241589/pgid-11464061')
