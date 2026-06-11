"""1. 查询 select —— 查询所有学生"""
import pymysql

con = pymysql.connect(
    host="localhost",
    port=3306,
    user="root",
    password="yzk058310!",
    database="db_python",
    charset="utf8mb4"
)

try:
    cursor = con.cursor()
    cursor.execute("select * from student_trp")
    result = cursor.fetchall()
    print(type(result), result)
    print()

    for row in result:
        print(row)

finally:
    con.close()
