"""4. 修改 update —— 所有人生日年份减 1，相当于年龄 +1"""
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

    sql = """
    update student_trp
    set birthday = date_sub(birthday, interval 1 year)
    """

    cursor.execute(sql)
    con.commit()

    print("修改成功，影响行数：", cursor.rowcount)

finally:
    con.close()
