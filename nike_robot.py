# coding:utf-8

"""
redis (2.10.5)
requests (2.9.1)
python (2.7.10)

"""

import requests
import logging
import json
import time
from threading import Thread
import re
import sys
import redis
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
LOGIN_URL = 'https://unite.nike.com/loginWithSetCookie?'\
            'locale=zh_CN&backendEnvironment=default'
# 提交订单地址
PUT_ORDER_URL = 'https://secure-store.nike.com/ap/services/jcartService?' \
                'callback=nike_Cart_handleJCartResponse' \
                '&action=addItem&lang_locale=zh_CN&country=CN&catalogId=4&' \
                'siteId=94&passcode=null&sizeType=null&qty=1&rt=json&'\
                'view=3&displaySize=43'
MAX_FAIL_TIMES = 100  # 每个下单线程的最大失败重试次数
MAX_RETRY_TIMES = 200  # 每个下单线程的重新提交订单次数
# 是否开启调试模式
DEBUG = False

# 全局session
session = requests.Session()
# 添加购物车订阅实例
r = redis.StrictRedis('localhost', 6379)
p = r.pubsub()


class NikeLoginParam(object):
    """登录时, 请求body体参数"""

    def __init__(self, username, password, client_id):
        super(NikeLoginParam, self).__init__()
        self.username = username
        self.password = password
        self.client_id = client_id
        self.grant_type = 'password'
        self.keepMeLoggedIn = True
        self.ux_id = 'com.nike.commerce.nikedotcom.web'

    def to_json(self):
        return json.dumps(self, default=lambda o: o.__dict__,
                          sort_keys=True, indent=4)

    def __repr__(self):
        return '此次登陆用户名:%s, 密码:%s' % (self.username, self.password)


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
        p.subscribe('addToCart-notice-channel')
        while True:
            if fail_times >= MAX_FAIL_TIMES:
                break
            if retry_times >= MAX_RETRY_TIMES:
                break
            # 某个线程下单成功了, 所以直接退出
            message = p.get_message()
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
                        r.publish('addToCart-notice-channel', 'SUCCESS')
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
                            r.publish('addToCart-notice-channel', 'FAIL')
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
    res = session.post(LOGIN_URL, data=param.to_json(), headers=HEADERS)
    end = time.time()
    status_code = res.status_code
    if status_code == 200:
        content = res.content
        key_token = json.loads(content)['access_token']
        if json.loads(content)['access_token'] is not None:
            # logging.info(res.cookies)
            logging.info('登陆Nike官网成功,耗时%s秒', (end - start))
    else:
        key_token = None
        logging.error('登陆Nike官网失败[%s]!', status_code)
    return key_token


# 通过产品页面获取产品信息, 构造提交订单参数
def parse_product_info(product_detail_url):
    logging.info("开始访问:%s 获取商品信息.", product_detail_url)
    raw_pd_id_pattern = re.compile('pid-(\d{8})\/')
    pd_id_match = raw_pd_id_pattern.search(product_detail_url)
    if pd_id_match:
        raw_pd_id = pd_id_match.group(1)
        start = time.time()
        res = session.get(product_detail_url, headers=HEADERS)
        end = time.time()
        status_code = res.status_code
        if status_code == 200:
            content = res.content
            # 匹配出鞋子的基本信息; 名称以及43码对应的货号
            shoe_base_info_pattern = re.compile('name="price"\svalue="(?P<price>.*?)"[.|\s\S]*name="line1" '
                                                'value="(?P<name>.*?)"[.|\s\S]*name="line2" '
                                                'value="(?P<category>.*?)"\/>[.|\s\S]*"displaySize":"43","sku":"(?P<id>\d{8})"')
            shoe_base_info_match = shoe_base_info_pattern.search(content)
            if shoe_base_info_match:
                shoe_name = shoe_base_info_match.group('name')
                shoe_category = shoe_base_info_match.group('category')
                shoe_price = shoe_base_info_match.group('price')
                shoe_id = shoe_base_info_match.group('id')
                order_param = AddToCartParam(
                    0, shoe_price, shoe_id, shoe_name, shoe_category)
                logging.info('鞋子基本信息如下:')
                logging.info('名称:%s', shoe_name)
                logging.info('价格:%s', shoe_price)
                logging.info('--------------')
                # 抠出正还在售的鞋子清单正则
                in_stock_list_pattern = re.compile(
                    '<ul\sdata-status="IN_STOCK".*>(?P<in_stock_list>[.|\s\S]*?)<\/ul>')
                in_stock_list_matcher = in_stock_list_pattern.search(content)
                if in_stock_list_matcher:
                    in_stock_list = in_stock_list_matcher.group(
                        'in_stock_list')
                    if in_stock_list:
                        # logging.info(in_stock_list)
                        # 匹配出每个在售鞋子的信息, 比如id, 颜色等等
                        shoe_info_pattern = re.compile(
                            '<a\shref="(?P<info_url>.*?)"\sdata-productid="(?P<info_id>\d{8})"\stitle="(?P<shoe_color>.*?)"')
                        shoe_info_matcher = shoe_info_pattern.finditer(
                            in_stock_list)
                        if shoe_info_matcher:
                            logging.info('下面鞋子还有货:')
                            for matcher in shoe_info_matcher:
                                shoe_info = ShoeInfo(matcher.group('info_url'), matcher.group('info_id'),
                                                     matcher.group('shoe_color'))
                                # logging.info(shoe_info)
                                logging.info('鞋子ID: %s', shoe_info.info_id)
                                logging.info('鞋子颜色: %s ', shoe_info.shoe_color)
                                logging.info('鞋子地址:%s', shoe_info.info_url)
                                logging.info('--------------')
                                # if shoe_info_matcher:
                                #     logging.info(shoe_info_matcher.groups())
                            logging.info('获取商品信息完成, 耗时%s秒', (end - start))
                            order_param.productId = raw_input('请选择你要下单的鞋子ID:')
                            if raw_pd_id != order_param.productId:
                                # 要重新请求一次
                                new_res = session.get(product_detail_url.replace(
                                    raw_pd_id, order_param.productId))
                                # 匹配出鞋子的基本信息; 名称以及43码对应的货号
                                common_sku_pattern = re.compile(
                                    '"displaySize":"43","sku":"(?P<id>\d{8})"')
                                common_sku_match = common_sku_pattern.search(
                                    new_res.content)
                                if common_sku_match:
                                    order_param.skuId = common_sku_match.group(
                                        'id')
                                    order_param.skuAndSize = '%s:43' % order_param.skuId
                            return order_param
        else:
            logging.error('获取商品信息失败[%s]!', status_code)
            # page gone 鞋子已经售罄了或者Nike下掉该款鞋子的介绍页
            if status_code == 410:
                logging.warn("抱歉, 您查找的商品已不存在")
            sys.exit(1)  # 没有获取到商品信息,直接退出


if __name__ == '__main__':
    user_name = raw_input('请输入你的nike用户名:')
    password = raw_input('请输入你的nike密码:')
    nike_login_param = NikeLoginParam(
        user_name, password, 'HlHa2Cje3ctlaOqnxvgZXNaAs7T9nAuH')
    login(nike_login_param)
    pd_url = raw_input('请输入鞋子地址:')
    # pd_url = 'http://store.nike.com/cn/zh_cn/pd/' \
    #          'kobe-11-elite-low-%E7%94%B7%E5%AD%90%E7%AF%AE%E7%90%83%E9%9E%8B/pid-11053644/pgid-11181196'
    order_param = parse_product_info(pd_url)
    threads = []
    for i in range(1):
        thread = AddToCartTask(order_param, pd_url)
        thread.start()
    for thread in threads:
        thread.join()
