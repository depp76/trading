import subprocess
import getpass
import tempfile
import os
import shutil

def register_secret():
    print("=" * 60)
    print("Google Cloud Secret Manager - 시크릿 키 등록 도우미")
    print("=" * 60)
    
    # 1. 시크릿 이름 입력
    secret_name = input("\n새로 등록할 시크릿 키의 이름(예: OPENAI_API_KEY)을 입력하세요: ").strip()
    if not secret_name:
        print("이름이 입력되지 않아 취소합니다.")
        return
        
    # 2. 시크릿 값 프롬프트 (화면에 안보임)
    secret_value = getpass.getpass(f"\n[{secret_name}]의 실제 값(API Key 등)을 붙여넣으세요\n(보안을 위해 화면에 입력 내용이 보이지 않습니다. 붙여넣기 후 Enter): ")
    if not secret_value:
        print("값이 입력되지 않아 취소합니다.")
        return
        
    print(f"\n⏳ 구글 클라우드에 [{secret_name}] 시크릿 생성 중...")
    
    # 임시 파일로 값을 안전하게 전달
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile('w', delete=False, encoding='utf-8') as temp:
            temp.write(secret_value)
            temp_path = temp.name

        # gcloud 명령어 호출
        # shutil.which() resolves the .cmd/.bat extension (PATHEXT) so this
        # works on Windows without shell=True, which would otherwise pass
        # secret_name through cmd.exe and let shell metacharacters in it
        # (e.g. `&`, `|`) run as separate commands.
        gcloud_exe = shutil.which('gcloud') or 'gcloud'
        result = subprocess.run(
            [gcloud_exe, 'secrets', 'create', secret_name, f'--data-file={temp_path}'],
            capture_output=True, text=True, shell=False
        )

        if result.returncode == 0:
            print(f"✅ 성공적으로 [{secret_name}] 시크릿이 등록되었습니다!")
        else:
            if "already exists" in result.stderr:
                print(f"⚠️ 이미 [{secret_name}]라는 이름의 시크릿이 존재합니다. 새 버전을 추가합니다...")
                result_ver = subprocess.run(
                    [gcloud_exe, 'secrets', 'versions', 'add', secret_name, f'--data-file={temp_path}'],
                    capture_output=True, text=True, shell=False
                )
                if result_ver.returncode == 0:
                    print(f"✅ 기존 [{secret_name}]에 새로운 버전의 키 값이 성공적으로 업데이트되었습니다!")
                else:
                    print(f"❌ 버전 업데이트 실패: {result_ver.stderr}")
            else:
                print(f"❌ gcloud 등록 실패:\n{result.stderr}")
                
    except Exception as e:
        print(f"❌ 에러가 발생했습니다: {e}")
    finally:
        # 무조건 임시 파일 삭제하여 보안 유지
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

if __name__ == "__main__":
    register_secret()
