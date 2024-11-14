from datetime import datetime

def calculate_age(birth_date, current_date):
    # 生年月日から年齢を計算
    years = current_date.year - birth_date.year
    months = current_date.month - birth_date.month
    days = current_date.day - birth_date.day

    # 日付修正
    if days < 0:
        # 日数がマイナスの場合は月を調整
        months -= 1
        # 現在の月を取得
        previous_month = (current_date.month - 1) if current_date.month > 1 else 12
        # 月の最終日を取得
        days_in_previous_month = (current_date - datetime(current_date.year, previous_month, 1)).days
        days += days_in_previous_month

    # 月がマイナスの場合は年を調整
    if months < 0:
        years -= 1
        months += 12

    return years, months, days



def test_calculate_age():
    # テストケース1
    birth_date_1 = datetime(2010, 5, 15)
    current_date_1 = datetime(2023, 11, 10)
    assert calculate_age(birth_date_1, current_date_1) == (13, 5, 26)

    # テストケース2
    birth_date_2 = datetime(2000, 10, 20)
    current_date_2 = datetime(2023, 11, 10)
    assert calculate_age(birth_date_2, current_date_2) == (23, 0, 21)

    # テストケース3
    birth_date_3 = datetime(1995, 8, 8)
    current_date_3 = datetime(2023, 11, 10)
    assert calculate_age(birth_date_3, current_date_3) == (28, 3, 2)

    print("すべてのテストケースがパスしました。")

# テストを実行
test_calculate_age()

# 生年月日
birth_date = datetime(2010, 5, 15)
# 現在の日時
current_date = datetime(2023, 11, 10)

# 年齢を計算
age_years, age_months, age_days = calculate_age(birth_date, current_date)
print(f"{age_years}歳 {age_months}ヶ月 {age_days}日")

