"""Explicit inert rehearsal entry. Importing starts no service and reads no settings."""
import argparse
import sys
from app.monitoring.deployment_policy import load_policy,PolicyError


class _Parser(argparse.ArgumentParser):
    def error(self,message):
        self.exit(2,'Inert startup refused: invalid command line\n')


def application_from_policy(path):
    policy=load_policy(path)
    from app.api.app import create_inert_app
    return create_inert_app(policy)


def main(argv=None):
    parser=_Parser(description='Inert read-only rehearsal only')
    parser.add_argument('--policy',required=True)
    args=parser.parse_args(argv)
    try:application=application_from_policy(args.policy)
    except PolicyError:
        print('Inert startup refused: policy or public snapshot unavailable',file=sys.stderr)
        return 2
    import uvicorn
    uvicorn.run(application,host='0.0.0.0',port=8000,workers=1,reload=False,
        access_log=False,server_header=False,proxy_headers=False)
    return 0


if __name__=='__main__':raise SystemExit(main())
