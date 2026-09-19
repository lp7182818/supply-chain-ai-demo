import streamlit as st
import pandas as pd

st.title("我的第一个网页")

st.write("下面是一张表：")

df = pd.DataFrame({
    "供应商": ["腾讯", "搜狐", "联想"],
    "金额": [1600, 900, 440]
})
# 1. 输入框：打字时看下面那句话动不动（体会"整页重跑"）
名字 = st.text_input("请输入你的名字")
st.write("你好，", 名字)
供应商 = st.selectbox("选一个供应商", ["腾讯", "搜狐", "联想"])
st.write("你选了：", 供应商)
文件 = st.file_uploader("上传Excel", type=["xlsx"])
if 文件 is None:
    st.write("现在还没传，文件是 None")
else:
    st.write("收到文件：", 文件.name)