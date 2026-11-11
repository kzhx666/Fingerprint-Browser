using System;
using System.Text.Json;

namespace KZManager;

/// <summary>
/// 24. 自动化框架无缝连接器 (Automation Framework Connector)。
/// 一键获取 Playwright、Puppeteer、Selenium 的环境连接信息与即用代码片段。
/// </summary>
public static class FrameworkConnector
{
    public sealed class FrameworkInfo
    {
        public string InstanceName { get; set; } = "";
        public int CdpPort { get; set; }
        public string CdpEndpoint { get; set; } = "";
        public string PlaywrightPython { get; set; } = "";
        public string PlaywrightNode { get; set; } = "";
        public string PuppeteerNode { get; set; } = "";
        public string SeleniumPython { get; set; } = "";
    }

    public static FrameworkInfo GetConnectionInfo(string instanceName, int port = 9222)
    {
        var endpoint = $"http://127.0.0.1:{port}";
        return new FrameworkInfo
        {
            InstanceName = instanceName,
            CdpPort = port,
            CdpEndpoint = endpoint,
            PlaywrightPython = $@"# Playwright (Python) 连接代码
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp('{endpoint}')
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.new_page()
    page.goto('https://example.com')
",
            PlaywrightNode = $@"// Playwright (Node.js) 连接代码
const {{ chromium }} = require('playwright');

(async () => {{
  const browser = await chromium.connectOverCDP('{endpoint}');
  const context = browser.contexts()[0];
  const page = context.pages()[0] || await context.newPage();
  await page.goto('https://example.com');
}})();
",
            PuppeteerNode = $@"// Puppeteer (Node.js) 连接代码
const puppeteer = require('puppeteer-core');

(async () => {{
  const browser = await puppeteer.connect({{ browserURL: '{endpoint}' }});
  const pages = await browser.pages();
  const page = pages[0] || await browser.newPage();
  await page.goto('https://example.com');
}})();
",
            SeleniumPython = $@"# Selenium 4 (Python) 连接代码
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

options = Options()
options.add_experimental_option('debuggerAddress', '127.0.0.1:{port}')
driver = webdriver.Chrome(options=options)
driver.get('https://example.com')
"
        };
    }
}
