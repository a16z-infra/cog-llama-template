.PHONY: init 
.PHONY: select
.PHONY: test-local
.PHONY: push
.PHONY: push-and-test
.PHONY: clean

# this is required to build sentencepiece for py3.11
# requires cog > 0.9.0-beta1
# get it at https://github.com/replicate/cog/releases/download/v0.9.0-beta1/cog_linux_x86_64
export COG_EXPERIMENTAL_BUILD_STAGE_DEPS = apt update && apt install -yy cmake google-perftools
export FAKE_COG_VERSION = 0.8.1

CURRENT_DIR := $(shell basename $(PWD))

ifeq ($(findstring cog,$(CURRENT_DIR)),cog)
IMAGE_NAME := $(CURRENT_DIR)
else
IMAGE_NAME := cog-$(CURRENT_DIR)
endif

REPLICATE_USER ?= replicate-internal

model ?= $(SELECTED_MODEL)

ifeq ($(findstring chat,$(model)),chat)
    schema := chat-schema.json
else
    schema := base-schema.json
endif

base-schema.json:
	$(MAKE) select model=llama-2-7b
	cog run --use-cuda-base-image=false python3 -m cog.command.openapi_schema > base-schema.json
chat-schema.json:
	$(MAKE) select model=llama-2-7b-chat
	cog run --use-cuda-base-image=false python3 -m cog.command.openapi_schema > chat-schema.json
	

init:
	@if [ -z "$(model)" ]; then \
		echo "Error: 'model' argument must be specified or 'MODEL_ENV' environment variable must be set. E.g., make select model=your_model_name or export MODEL_ENV=your_model_name"; \
		exit 1; \
	fi
	# Initialize directory for model
	mkdir -p models/$(model)
	cp -r model_templates/*  models/$(model)
	if [ -e model_templates/.env ]; then cp model_templates/.env models/$(model) ; fi
	if [ -e model_templates/.dockerignore ]; then \
		cp model_templates/.dockerignore models/$(model); \
	else \
		touch models/$(model)/.dockerignore; \
	fi
	printf "\n# Generated by 'make init'\n" >> models/$(model)/.dockerignore
	printf "/models/*/\n" >> models/$(model)/.dockerignore
	printf "!/models/$(model)/\n" >> models/$(model)/.dockerignore
	printf "/models/$(model)/model_artifacts/**\n" >> models/$(model)/.dockerignore
	printf "!/models/$(model)/model_artifacts/tokenizer/\n" >> models/$(model)/.dockerignore

	mkdir -p models/$(model)/model_artifacts/tokenizer
	cp -r llama_weights/tokenizer/* models/$(model)/model_artifacts/tokenizer

update:
	@if [ -z "$(model)" ]; then \
		echo "Error: 'model' argument must be specified or 'MODEL_ENV' environment variable must be set. E.g., make select model=your_model_name or export MODEL_ENV=your_model_name"; \
		exit 1; \
	fi
	cp -r model_templates/*  models/$(model)
	
model_dir=models/$(model)

select:
	@if [ -z "$(model)" ]; then \
		echo "Error: 'model' argument must be specified or 'MODEL_ENV' environment variable must be set. E.g., make select model=your_model_name or export MODEL_ENV=your_model_name"; \
		exit 1; \
	fi
	# this approach makes copies
	# rsync -av --exclude 'model_artifacts/' models/$(model)/ .

	# this approach behaves the same way but makes symlinks
	# # if we also wanted to copy directory structure we could do this, but we only need one dir deep
	# rsync -av --exclude 'model_artifacts/' --include '*/' --exclude '*' $(model_dir)/ .
	# For symlinking files
	find $(model_dir) -type f ! -path "$(model_dir)/model_artifacts/*" -exec ln -sf {} . \;
	# For specific files like .env and .dockerignore, we link them if they exist
	[ -e $(model_dir)/.env ] && ln -sf $(model_dir)/.env .env || true
	rm .dockerignore || true
	[ -e $(model_dir)/.dockerignore ] && cat model_templates/.dockerignore $(model_dir)/.dockerignore > .dockerignore || true
	

	#cog build
	@echo "#########Selected model: $(model)########"

clean: select
	if [ -e models/$(model)/model_artifacts/default_inference_weights]; then sudo rm -rf models/$(model)/model_artifacts/default_inference_weights; fi
	if [ -e models/$(model)/model_artifacts/training_weights]; then  sudo rm -rf models/$(model)/model_artifacts/training_weights; fi
	if [ -e training_output.zip]; then sudo rm -rf training_output.zip; fi

build-local: select
	cog build --openapi-schema=$(schema) --use-cuda-base-image=false --progress plain

serve: select
	docker run \
	-ti \
	-p 5000:5000 \
	--gpus=all \
	-e COG_WEIGHTS=http://$(HOST_NAME):8000/training_output.zip \
	-v `pwd`/training_output.zip:/src/local_weights.zip \
	$(IMAGE_NAME)

test-local-predict: 
	cog build
	@if [ "$(verbose)" = "true" ]; then \
		pytest ./tests/test_predict.py -s; \
	else \
		pytest ./tests/test_predict.py; \
	fi

test-local-train: 
	cog build
	rm -rf training_output.zip
	@if [ "$(verbose)" = "true" ]; then \
		pytest ./tests/test_train.py -s; \
	else \
		pytest ./tests/test_train.py; \
	fi

test-local-train-predict: build-local
	@if [ "$(verbose)" = "true" ]; then \
		pytest ./tests/test_train_predict.py -s; \
	else \
		pytest ./tests/test_train_predict.py; \
	fi

test-local: select test-local-predict test-local-train test-local-train-predict

stage: select
	@echo "Pushing $(model) to r8.im/$(REPLICATE_USER)/staging-$(model)..."
	cog push --openapi-schema=$(schema) --use-cuda-base-image=false --progress plain r8.im/$(REPLICATE_USER)/staging-$(model)

test-stage-predict:
	@if [ "$(verbose)" = "true" ]; then \
		pytest tests/test_remote_predict.py -s --model $(REPLICATE_USER)/staging-$(model); \
	else \
		pytest tests/test_remote_predict.py --model $(REPLICATE_USER)/staging-$(model); \
	fi

test-stage-train-predict:
	@if [ "$(verbose)" = "true" ]; then \
		pytest tests/test_remote_train.py -s --model $(REPLICATE_USER)/staging-$(model); \
	else \
		pytest tests/test_remote_train.py --model $(REPLICATE_USER)/staging-$(model); \
	fi

test-stage: test-stage-predict test-stage-train-predict


stage-and-test-models:
	$(foreach model, $(subst ,, $(models)), \
		$(MAKE) select model=$(model); \
		$(MAKE) stage model=$(model); \
		$(MAKE) test-stage model=$(model); \
	)
	
push: select
	cog push --openapi-schema=$(schema) --use-cuda-base-image=false --progress plain r8.im/$(REPLICATE_USER)/$(model)

test-push: test-local push
	
test-live:
	python test/push_test.py

push-and-test: push test-live

help:
	@echo "Available targets:\n\n"
	@echo "init: Create the model directory."
	@echo "   e.g., \`make init dir=<model_dir>\`"

mypush:
        docker build -t us.gcr.io/replicate/wordframe:$(version) .
        docker push us.gcr.io/replicate/wordframe:$(version)
	# make select whatever
       
