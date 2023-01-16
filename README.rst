========
Dumpmon
========

はじめに
--------

私の子供が保育園に通いはじめたら、園と保護者の連絡にはコドモンというアプリを使うように指示されました。
手書きの連絡帳も味がありますが、ITを使った連絡帳も便利ですね。
朝はコドモンで子供の様子を送信して、夕方には園から一日の様子が送られてきます。

便利なコドモンですが、使っているうちにもう少しこうできたらいいのになと思うことがありました。
毎日送られてくる添付ファイルを一つずつ保存するのが煩わしかったり、
園での子供の様子を祖父母にも転送してあげたいと思ってもコピーペーストができなかったり、
といった細かなことです。

そこで、コドモンにはWEB版もあったので、これをスクレイピングしてローカル端末上であれこれしたいとおもいました。


注意
-----
スクレイピングでコドモンのウェブサーバーから情報を保存しています。
高頻度の連続アクセスでサーバーに高い負荷をかけないようにアクセスには１秒ごとのインターバルをおいています。


使い方
------

| python dumpmon.py
| 
| ~/Desktop/dumomon に保存されます。
| 
| dumpmon/dump/ には、サーバーから取得した生のデータが保存されます。
| dumpmon/output/ には、連絡帳と添付ファイルが人月ごとにまとめられて、rst形式で保存されます。



オプション
-----------

::

    usage: dumpmon.py [-h] [-f] [-dl] [-m] [-a | -d DAY | -r YYYY-MM-DD YYYY-MM-DD] [-v]

    Fetches and dumps codmon data.

    options:
    -h, --help            show this help message and exit
    -v, --verbosity       increase output verbosity

    phase:
    Limit the execution phase

    -f, --fetch           fetch json
    -dl, --download       download attachment file
    -m, --makenote        make communication notebook

    daterange:
    Fetch Date Range

    -a, --all             Retrieve all data up to the present day
    -d DAY, --day DAY     Retrieve data for a specified number of days up to today
    -r YYYY-MM-DD YYYY-MM-DD, --range YYYY-MM-DD YYYY-MM-DD
                            Obtain data for a specified date range

