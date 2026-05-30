"""
一个支持加、减、乘、除四种基本运算的计算器。
既可作为模块导入使用，也可通过命令行交互方式使用。
"""

from typing import Union

Number = Union[int, float]


def add(a: Number, b: Number) -> Number:
    """加法运算"""
    return a + b


def subtract(a: Number, b: Number) -> Number:
    """减法运算"""
    return a - b


def multiply(a: Number, b: Number) -> Number:
    """乘法运算"""
    return a * b


def divide(a: Number, b: Number) -> float:
    """
    除法运算。
    如果除数为零，抛出 ZeroDivisionError。
    """
    if b == 0:
        raise ZeroDivisionError("除数不能为零。")
    return a / b


def get_number(prompt: str) -> Number:
    """
    从用户输入中获取一个数字。
    支持整数和浮点数，对非法输入进行重试。
    """
    while True:
        user_input = input(prompt).strip()
        try:
            if "." in user_input:
                return float(user_input)
            else:
                return int(user_input)
        except ValueError:
            print(f"错误：'{user_input}' 不是一个有效的数字，请重新输入。")


def print_result(operation: str, a: Number, b: Number, result: Number) -> None:
    """格式化输出计算结果。"""
    print(f"计算结果：{a} {operation} {b} = {result}")


def run_cli() -> None:
    """运行命令行交互式计算器。"""
    menu = """
===================================
       简易计算器（Python 版）
===================================
  1 — 加法 (+)
  2 — 减法 (-)
  3 — 乘法 (×)
  4 — 除法 (÷)
  5 — 退出
-----------------------------------"""

    operations = {
        "1": ("+", add),
        "2": ("-", subtract),
        "3": ("×", multiply),
        "4": ("÷", divide),
    }

    while True:
        print(menu)
        choice = input("请选择运算类型（1-5）：").strip()

        if choice == "5":
            print("感谢使用，再见！")
            break

        if choice not in operations:
            print("错误：请输入 1-5 之间的数字。")
            continue

        symbol, func = operations[choice]

        a = get_number("请输入第一个数字：")
        b = get_number("请输入第二个数字：")

        try:
            result = func(a, b)
        except ZeroDivisionError as e:
            print(f"错误：{e}")
            continue

        print_result(symbol, a, b, result)


if __name__ == "__main__":
    run_cli()
