"""2. 增加 insert —— 新增一个好友"""
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
    insert into student_trp(stu_no, name, birthday)
    values ('2315107199', '孙小美', '2005-06-18')
    """

    cursor.execute(sql)
    con.commit()

    print("新增成功，影响行数：", cursor.rowcount)

finally:
    con.close()
