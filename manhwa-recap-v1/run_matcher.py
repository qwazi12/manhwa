import argparse
import matcher

def main():
    parser = argparse.ArgumentParser(description="Run semantic/lexical matcher to map beats to panels")
    parser.add_argument("--beats", required=True, help="Path to beats.json")
    parser.add_argument("--descriptions", required=True, help="Path to descriptions.json")
    parser.add_argument("--out", required=True, help="Path to output beatsheet.json")
    parser.add_argument("--embed-model", default=None, help="Name of sentence-transformers embedding model")
    
    args = parser.parse_args()
    matcher.run(args.beats, args.descriptions, args.out, args.embed_model)

if __name__ == "__main__":
    main()
