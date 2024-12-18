import os
import re
import time
import json
import csv
import requests
import pandas as pd

from flask import Flask, request, render_template, jsonify
from bokeh.plotting import figure
from bokeh.models import ColumnDataSource, HoverTool, RangeSlider, CustomJS
from bokeh.embed import components
from bokeh.layouts import column
from bokeh.resources import CDN
from bokeh.models import Range1d
from bokeh.models import Range1d, FixedTicker 

app = Flask(__name__)

CLIENT_ID = 'kd1unb4b3q4t58fwlpcbzcbnm76a8fp'

def process_comments(edges, comment_count_by_minute):
    for comment in edges:
        node = comment['node']
        offset_seconds = node.get('contentOffsetSeconds')
        if offset_seconds is not None:
            minute = offset_seconds // 60
            comment_count_by_minute[minute] = comment_count_by_minute.get(minute, 0) + 1

def fetch_comments(video_id):
    csv_filename = f"{video_id}.csv"
    if os.path.exists(csv_filename):
        print(f"Using existing CSV for {video_id}")
        return csv_filename

    print(f"Fetching comments for Video ID: {video_id}")
    api_url = "https://gql.twitch.tv/gql"
    first_data = json.dumps([
        {
            "operationName": "VideoCommentsByOffsetOrCursor",
            "variables": {
                "videoID": video_id,
                "contentOffsetSeconds": 0
            },
            "extensions": {
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"
                }
            }
        }
    ])

    def get_json_data(video_id, cursor):
        loop_data = json.dumps([
            {
                "operationName": "VideoCommentsByOffsetOrCursor",
                "variables": {
                    "videoID": video_id,
                    "cursor": cursor
                },
                "extensions": {
                    "persistedQuery": {
                        "version": 1,
                        "sha256Hash": "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"
                    }
                }
            }
        ])
        return(loop_data)

    comment_count_by_minute = {}
    session = requests.Session()
    session.headers = {'Client-ID': CLIENT_ID, 'content-type': 'application/json'}

    response = session.post(api_url, first_data, timeout=10)
    response.raise_for_status()
    data = response.json()

    if not data or 'data' not in data[0] or data[0]['data']['video'] is None:
        print("No video data found.")
        return None

    comments_data = data[0]['data']['video']['comments']
    if comments_data is None or 'edges' not in comments_data:
        print("No comments data found.")
        return None

    def handle_edges(edges):
        process_comments(edges, comment_count_by_minute)
        total_comments = sum(comment_count_by_minute.values())
        print(f"Current total comments: {total_comments}")

    handle_edges(comments_data['edges'])

    cursor = None
    if comments_data['pageInfo']['hasNextPage']:
        cursor = comments_data['edges'][-1]['cursor']
        time.sleep(0.1)

    while cursor:
        response = session.post(api_url, get_json_data(video_id, cursor), timeout=10)
        response.raise_for_status()
        data = response.json()

        if not data or 'data' not in data[0] or data[0]['data']['video'] is None:
            break

        comments_data = data[0]['data']['video']['comments']
        if comments_data is None or 'edges' not in comments_data:
            break

        handle_edges(comments_data['edges'])

        if comments_data['pageInfo']['hasNextPage']:
            cursor = comments_data['edges'][-1]['cursor']
            time.sleep(0.1)
        else:
            cursor = None

    with open(csv_filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["minute", "comment_count"])
        for minute, count in sorted(comment_count_by_minute.items()):
            writer.writerow([minute, count])

    return csv_filename

from bokeh.models import Range1d  # 追加でインポート

def create_bokeh_plot(video_id):
    csv_filename = f"{video_id}.csv"
    df = pd.read_csv(csv_filename)
    avg_count = df['comment_count'].mean()
    minutes = df['minute']
    comment_counts = df['comment_count']

    colors = ['red' if c > avg_count else 'blue' for c in comment_counts]
    formatted_labels = [f"{m//60:02d}:{m%60:02d}:00" for m in minutes]

    source = ColumnDataSource(data=dict(
        minute=minutes,
        comment_count=comment_counts,
        color=colors,
        time_str=formatted_labels,
        offset_seconds=[m * 60 for m in minutes]  # 秒単位でオフセットを追加
    ))

    # ツールバーを有効にし、ホイールズームを標準で有効化
    p = figure(
        title=f"Comments per Minute (Video ID: {video_id})",
        x_axis_label="Time (mm:ss)",
        y_axis_label="Comment Count",
        tools="pan,wheel_zoom,box_zoom,reset,tap,save",  # 必要なツールを追加
        toolbar_location="above",  # ツールバーの位置（上部）
        active_scroll="wheel_zoom",  # ホイールズームを標準で有効化
        sizing_mode="stretch_width",
        height=600,
        y_range=Range1d(start=0, end=max(comment_counts) * 1.1)  # Y軸の下限を固定
    )

    p.title.text_font_size = "18pt"
    p.xaxis.axis_label_text_font_size = "12pt"
    p.yaxis.axis_label_text_font_size = "12pt"
    p.xaxis.major_label_text_font_size = "10pt"
    p.yaxis.major_label_text_font_size = "10pt"

    p.vbar(x='minute', top='comment_count', source=source, width=0.9, color='color')

    # 平均線を描画
    p.ray(x=0, y=avg_count, length=0, angle=0, color='green', line_dash='dashed', line_width=2)

    # ホバーツールの設定
    hover = HoverTool(tooltips=[
        ("Time", "@time_str"),
        ("Comments", "@comment_count"),
    ])
    p.add_tools(hover)

    # クリックイベントの設定
    callback = CustomJS(
        args=dict(source=source, video_id=video_id, parent_domain=request.host.split(':')[0]),
        code="""
            const indices = source.selected.indices;
            if (indices.length > 0) {
                const index = indices[0];
                const offsetSeconds = source.data['offset_seconds'][index];
                const twitchPlayer = document.getElementById('twitch-player');
                const url = `https://player.twitch.tv/?video=${video_id}&time=${Math.floor(offsetSeconds)}s&parent=${parent_domain}`;
                twitchPlayer.src = url;
            } else {
                console.log("No data point selected. Click ignored.");
            }
        """
    )
    source.selected.js_on_change('indices', callback)

    # X軸の設定
    min_minute = int(minutes.min())
    max_minute = int(minutes.max())
    time_step = 5
    x_ticks = list(range(min_minute, max_minute+1, time_step))
    x_tick_labels = [f"{m//60:02d}:{m%60:02d}:00" for m in x_ticks]
    p.xaxis.ticker = x_ticks
    p.xaxis.major_label_overrides = {x: l for x, l in zip(x_ticks, x_tick_labels)}

    layout = column(p, sizing_mode="stretch_width")
    script, div = components(layout)
    return script, div

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start', methods=['POST'])
def start():
    user_input = request.form.get('video_id')
    video_id_pattern = r"https:\/\/www\.twitch\.tv\/videos\/(\d+)$"
    match = re.match(video_id_pattern, user_input)
    if match:
        video_id = match.group(1)
    else:
        video_id = user_input
    return render_template('start.html', video_id=video_id)

@app.route('/do_retrieval')
def do_retrieval():
    video_id = request.args.get('video_id', None)
    if not video_id:
        return jsonify({"status":"error","message":"No video_id provided"}),400

    csv_file = fetch_comments(video_id)
    if csv_file is None:
        return jsonify({"status":"error","message":"No data could be retrieved"}),400

    return jsonify({"status":"done","video_id":video_id})

@app.route('/plot_done')
def plot_done():
    video_id = request.args.get('video_id', None)
    if not video_id:
        return "No video_id provided", 400

    script, div = create_bokeh_plot(video_id)

    # 埋め込みに必要な親ドメインを動的に取得
    parent_domain = request.host.split(':')[0]  # ホスト名のみ取得（例: localhost）
    
    js_resources = CDN.js_files
    css_resources = CDN.css_files

    return render_template(
        'plot.html',
        script=script,
        div=div,
        video_id=video_id,
        parent_domain=parent_domain,
        js_resources=js_resources,
        css_resources=css_resources
    )

if __name__ == '__main__':
    app.run(debug=True)