"""3. 删除 delete —— 删除自己（杨正科）那一行"""
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

    sql = "delete from student_trp where stu_no = '2315107132'"

    cursor.execute(sql)
    con.commit()

    print("删除成功，影响行数：", cursor.rowcount)

finally:
    con.close()
