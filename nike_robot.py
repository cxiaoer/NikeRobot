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
import pdb

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s  '
                           '%(threadName)s  '
                           '%(levelname)s  '
                           '%(message)s')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_5) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/51.0.2704.106 Safari/537.36'
}
# 登陆check 地址
LOGIN_URL = 'https://www.nike.com/profile/login?Content-Locale=zh_CN'
# 提交订单地址
PUT_ORDER_URL = 'https://secure-store.nike.com/ap/services/jcartService?' \
                'callback=nike_Cart_handleJCartResponse' \
                '&action=addItem&lang_locale=zh_CN&country=CN&catalogId=4&' \
                'siteId=94&passcode=null&sizeType=null&qty=1&rt=json&' \
                'view=3&displaySize=43'
MAX_FAIL_TIMES = 100  # 每个下单线程的最大失败重试次数
MAX_RETRY_TIMES = 200  # 每个下单线程的重新提交订单次数
# 是否开启调试模式
DEBUG = False

# 全局session
session = requests.Session()


# 添加购物车订阅实例
# r = redis.StrictRedis('localhost', 6379)
# p = r.pubsub()


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


class AddToCartParam(object):
    """下单请求参数"""

    def __init__(self, product_id, price, sku_id, line1, line2):
        super(AddToCartParam, self).__init__()
        self.productId = product_id
        self.price = price
        self.skuId = sku_id
        self.skuAndSize = '%s:43' % sku_id
        self.line1 = line1
        self.line2 = line2


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


class AddToCartTask(Thread):
    """添加到购物车线程,主要负责鞋子添加到购物车中, 抢鞋的时候,添加购物车成功基本就算抢到了"""

    def __init__(self, param, pd_url):
        super(AddToCartTask, self).__init__()
        self.param = param
        self.pd_url = pd_url

    def run(self):
        retry_times = 1  # 尝试提交订单次数
        fail_times = 1  # 订单提交异常失败次数

        HEADERS['Referer'] = self.pd_url  # 带上Referer试试能否成功, 不带这个头, 会一直报429
        # p.subscribe('addToCart-notice-channel')
        while True:
            if fail_times >= MAX_FAIL_TIMES:
                break
            if retry_times >= MAX_RETRY_TIMES:
                break
            # 某个线程下单成功了, 所以直接退出
            # message = p.get_message()
            message = None
            if message:
                if message['data'] == 'SUCCESS' or message['data'] == 'FAIL':
                    break
            start = time.time()
            logging.info('[第%s次] 开始尝试添加到购物车了', retry_times)
            add_to_cart_url = PUT_ORDER_URL + \
                              '&_=' + str(int(1000 * time.time()))
            if DEBUG:
                pdb.set_trace()
            res = session.get(
                add_to_cart_url, params=self.param.__dict__, headers=HEADERS)
            logging.info(res.url)
            end = time.time()
            status_code = res.status_code
            # r.publish('addToCart-notice-channel', 'SUCCESS')
            if status_code == 200:
                res_status_pattern = re.compile('"status" :"(?P<status>.*?)"')
                logging.info(res.content)
                res_status_match = res_status_pattern.search(res.content)
                if res_status_match:
                    res_status = res_status_match.group('status')
                    if res_status == 'success':  # 添加到购物车成功
                        logging.info('[第%s次] 恭喜添加购物车成功.......; 耗时%s秒',
                                     retry_times, (end - start))
                        # r.publish('addToCart-notice-channel', 'SUCCESS')
                        break
                    elif res_status == 'wait':  # 下单需要排队,继续下单
                        logging.info("排队中......")
                    elif res_status == 'failure':
                        # 截取失败信息
                        fail_message_pattern = re.compile('message"\s:"(.*?)"')
                        fail_message_match = fail_message_pattern.search(
                            res.content)
                        if fail_message_match:
                            logging.info('添加购物车失败; 原因:%s',
                                         fail_message_match.group(1))
                            # r.publish('addToCart-notice-channel', 'FAIL')
                            break
                    retry_times += 1
            else:
                logging.info('[第%s次] 提交订单异常[%s]!', fail_times, status_code)
                fail_times += 1
                time.sleep(1)  # 隔一秒进行重新提交


# 登陆获取关键token参数
def login(param):
    logging.info('开始登陆Nike官网了.......')
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
            # logging.info(res.cookies)
            logging.info('登陆Nike官网成功,耗时%s秒', (end - start))
    else:
        # key_token = None
        logging.error('登陆Nike官网失败[%s]!', status_code)


# 清洗html; 去掉空格以及script标签
def clean_html(html_content):
    return html_content


def get_order_param(product_index_url):
    pass
    # selected_product_id=


def reg_match(content, regex):
    pass


# List<Dict>
# new RegMatcher(regex).matcher(content).get_value('')
# new RegMatcher(regex).matcher(content).get

if __name__ == '__main__':
    user_name = input('请输入你的nike用户名:')
    password = input('请输入你的nike密码:')
    nike_login_param = NikeLoginParam(
        user_name, password, '')
    login(nike_login_param)
    pd_url = input('请输入鞋子地址:')
    order_param = None
    threads = []
    for i in range(1):
        thread = AddToCartTask(order_param, pd_url)
        thread.start()
    for thread in threads:
        thread.join()
