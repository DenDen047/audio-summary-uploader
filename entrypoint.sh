#!/bin/bash -e
SHELL_NAME='entrypoint.sh'
echo "[$SHELL_NAME] START"

# ユーザID、ユーザ名の設定
if getent passwd "$USER_ID" > /dev/null 2>&1; then
    echo "[$SHELL_NAME] USER_ID '$USER_ID' already exists."
else
    echo "[$SHELL_NAME] USER_ID '$USER_ID' does NOT exist. So set UID of $USER_NAME to $USER_ID."
    sudo groupmod -g $GROUP_ID $USER_NAME
    sudo sed -i  -e "s/user:x:1000/user:x:$USER_ID/g" /etc/passwd
fi

# コマンドラインに着色（任意）
sudo sed -i  -e "s/#force_color_prompt=yes/force_color_prompt=yes/g" /home/user/.bashrc

# ピープ音消去（任意）
echo 'set bell-style none' > /home/$USER_NAME/.inputrc

echo "[$SHELL_NAME] FINISH"
exec $@